"""NLLB-200 翻譯引擎(與 Discord 解耦)。

職責
----
- 模型**延遲載入**:第一次翻譯時才把模型搬上 GPU,平時不佔 VRAM
- 語言**自動偵測**:langdetect → FLORES-200 語言碼
- 語言碼對應表:常用語言集、langdetect 碼、旗幟 emoji
- GPU 推論**序列化**:asyncio.Lock 避免並發 CUDA OOM;載入與推論都走 to_thread,不卡 event loop

NLLB(M2M100 架構)用法重點:tokenizer 要先設 src_lang,generate 要傳
forced_bos_token_id=tokenizer.convert_tokens_to_ids(目標碼)。
(`lang_code_to_id` 在新版 transformers 已不適用於 AutoTokenizer,故用 convert_tokens_to_ids。)
"""

from __future__ import annotations

import asyncio
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__, channel="translate")


# === 常用語言集:(顯示名, FLORES-200 碼) ===
COMMON_LANGS: list[tuple[str, str]] = [
    ("繁體中文", "zho_Hant"),
    ("简体中文", "zho_Hans"),
    ("English", "eng_Latn"),
    ("日本語", "jpn_Jpan"),
    ("한국어", "kor_Hang"),
    ("ไทย", "tha_Thai"),
    ("Tiếng Việt", "vie_Latn"),
    ("Français", "fra_Latn"),
    ("Deutsch", "deu_Latn"),
    ("Español", "spa_Latn"),
    ("Русский", "rus_Cyrl"),
    ("Bahasa Indonesia", "ind_Latn"),
]

# FLORES 碼 → 中文顯示名(回覆 embed 用)
FLORES_TO_NAME: dict[str, str] = {code: name for name, code in COMMON_LANGS}

# langdetect 回傳碼 → FLORES 碼(用 langdetect 是因它能區分 zh-tw / zh-cn)
LANGDETECT_TO_FLORES: dict[str, str] = {
    "zh-tw": "zho_Hant",
    "zh-cn": "zho_Hans",
    "en": "eng_Latn",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    "th": "tha_Thai",
    "vi": "vie_Latn",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "es": "spa_Latn",
    "ru": "rus_Cyrl",
    "id": "ind_Latn",
}

# 旗幟 emoji → FLORES 碼(反應翻譯用)
FLAG_TO_FLORES: dict[str, str] = {
    "🇹🇼": "zho_Hant",
    "🇭🇰": "zho_Hant",
    "🇨🇳": "zho_Hans",
    "🇬🇧": "eng_Latn",
    "🇺🇸": "eng_Latn",
    "🇯🇵": "jpn_Jpan",
    "🇰🇷": "kor_Hang",
    "🇹🇭": "tha_Thai",
    "🇻🇳": "vie_Latn",
    "🇫🇷": "fra_Latn",
    "🇩🇪": "deu_Latn",
    "🇪🇸": "spa_Latn",
    "🇷🇺": "rus_Cyrl",
    "🇮🇩": "ind_Latn",
}


def detect_flores(text: str) -> Optional[str]:
    """偵測文字語言並轉成 FLORES-200 碼;偵測失敗或不在常用集回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        # 延遲 import:langdetect 第一次 import 會載入語言模型
        from langdetect import detect, DetectorFactory, LangDetectException

        DetectorFactory.seed = 0  # 固定種子,避免短文偵測結果跳動
        code = detect(text)
    except LangDetectException:
        return None
    except Exception as e:
        logger.warning(f"語言偵測失敗：{e.__class__.__name__} - {e}")
        return None
    return LANGDETECT_TO_FLORES.get(code)


class NLLBEngine:
    """NLLB-200 翻譯引擎:延遲載入 + GPU 推論序列化。"""

    def __init__(self, model_name: str, device_pref: str = "auto"):
        self.model_name = model_name
        self.device_pref = device_pref
        self._model = None
        self._tokenizer = None
        self._device = None
        # 序列化 GPU 推論:同時間只跑一個 generate,避免並發 CUDA OOM
        self._lock = asyncio.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def device(self) -> Optional[str]:
        return self._device

    def _resolve_device(self) -> str:
        import torch

        if self.device_pref == "auto":
            return "cuda" if torch.cuda.is_available() else "cpu"
        return self.device_pref

    def _load_sync(self) -> None:
        """阻塞式載入模型(必須在 executor / to_thread 內呼叫)。"""
        if self._model is not None:
            return

        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        device = self._resolve_device()
        # GPU 用 fp16 省 VRAM、加速;CPU 維持 fp32 確保正確
        dtype = torch.float16 if device == "cuda" else torch.float32

        logger.info(f"⏳ 載入 NLLB 模型 {self.model_name}(device={device}, dtype={dtype})...")
        tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        model = AutoModelForSeq2SeqLM.from_pretrained(self.model_name, dtype=dtype)
        model = model.to(device)
        model.eval()

        self._tokenizer = tokenizer
        self._model = model
        self._device = device
        logger.info(f"✅ NLLB 模型已載入到 {device}")

    def _translate_sync(self, text: str, src_code: str, tgt_code: str) -> str:
        """阻塞式翻譯(必須在 executor / to_thread 內呼叫,且已被 lock 保護)。"""
        import torch

        self._load_sync()
        tokenizer = self._tokenizer
        model = self._model

        tokenizer.src_lang = src_code
        inputs = tokenizer(
            text, return_tensors="pt", truncation=True, max_length=512
        ).to(model.device)

        bos_id = tokenizer.convert_tokens_to_ids(tgt_code)
        with torch.inference_mode():
            generated = model.generate(
                **inputs,
                forced_bos_token_id=bos_id,
                max_new_tokens=512,
                num_beams=4,
            )
        return tokenizer.batch_decode(generated, skip_special_tokens=True)[0]

    async def translate(self, text: str, src_code: str, tgt_code: str) -> str:
        """翻譯(序列化 GPU 推論,不阻塞 event loop)。"""
        async with self._lock:
            return await asyncio.to_thread(self._translate_sync, text, src_code, tgt_code)
