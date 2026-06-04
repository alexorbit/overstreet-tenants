"""Configurações centrais do OverStreet-Corretor-Agent."""
import os
from pathlib import Path

# --- Telegram ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# --- Paths (legacy — prefer infra.py for new code) ---
_base = Path(__file__).parent.parent
DATA_DIR = _base / "data"

# Legacy single-DB paths (kept for backward compat during migration)
DB_PATH = Path(os.getenv("DB_PATH", str(_base / "data" / "global" / "meta.db")))
MEMORY_DB_PATH = Path(os.getenv("MEMORY_DB_PATH", str(_base / "data" / "global" / "shared_memory.db")))
FSM_DB_PATH = Path(os.getenv("FSM_DB_PATH", str(_base / "data" / "global" / "fsm.db")))

# --- Qdrant ---
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
QDRANT_GRPC_HOST = os.getenv("QDRANT_GRPC_HOST", "")
QDRANT_GRPC_PORT = int(os.getenv("QDRANT_GRPC_PORT", "0"))
COLLECTION_NAME = "imoveis"  # Legacy default; infra.py overrides per-tenant
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

# --- NVIDIA NIM ---
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "qwen/qwen3.5-122b-a10b")
NVIDIA_FAST_MODEL = os.getenv("NVIDIA_FAST_MODEL", "mistralai/mistral-small-4-119b-2603")

# --- Groq (Whisper transcription — grátis e instantâneo) ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_WHISPER_MODEL = "whisper-large-v3-turbo"

# --- Bot ---
MAX_RESULTS = 5
DEFAULT_RADIUS_KM = 5.0

# --- Admin ---
ADMIN_IDS: list[int] = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]

# --- SMTP ---
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", "Ana OverStreet <noreply@overstreet.com.br>")
SMTP_ENABLED = bool(SMTP_USER and SMTP_PASS)

# --- Tipo maps (fonte única) ---

TIPO_MAP: dict[int, str] = {
    72: "Apto", 68: "Casa", 47: "Sobrado",
    75: "Salão Com.", 78: "Sobrado Cond.", 83: "Cobertura",
    85: "Casa Cond.", 66: "Terreno", 73: "Sobrado Cond.",
    64: "Ponto Com.", 69: "Flat", 76: "Loja", 79: "Terreno Cond.",
    48: "Studio", 49: "Kitchenette", 36: "Sala Com.",
    37: "Conj. Com.", 57: "Galpão", 84: "Kitnet",
    74: "Casa", 80: "Loja Cond.", 67: "Terreno Cond.",
    61: "Garagem", 62: "Garagem Cond.", 65: "Ponto Com. Cond.",
    60: "Box", 86: "Sobrado", 70: "Chácara", 35: "Sala",
    38: "Conjunto", 40: "Prédio", 71: "Fazenda", 82: "Cobertura Cond.",
    157: "Condomínio", 118: "Rural", 165: "Lote Cond.",
    160: "Cond. Fechado", 163: "Loteamento",
}

TIPO_NAMES: dict[str, str] = {
    "72": "Apartamento", "68": "Casa", "47": "Sobrado",
    "75": "Salão Comercial", "78": "Sobrado em Condomínio", "83": "Cobertura",
    "85": "Casa em Condomínio", "66": "Terreno", "73": "Sobrado em Condomínio",
    "64": "Ponto Comercial", "69": "Flat", "76": "Loja", "79": "Terreno Condomínio",
    "48": "Studio", "49": "Kitchenette", "36": "Sala Comercial",
    "37": "Conjunto Comercial", "57": "Galpão", "84": "Kitnet",
    "74": "Casa", "80": "Loja em Condomínio", "67": "Terreno Condomínio",
    "61": "Garagem", "62": "Garagem Condomínio", "65": "Ponto Comercial Condomínio",
    "60": "Box", "86": "Sobrado", "70": "Chácara", "35": "Sala",
    "38": "Conjunto", "40": "Prédio", "71": "Fazenda", "82": "Cobertura Condomínio",
    "157": "Condomínio", "118": "Rural", "165": "Lote Condomínio",
    "160": "Condomínio Fechado", "163": "Loteamento",
}

TIPO_GROUPS: dict[str, list[str]] = {
    "apartamento": ["72"],
    "apto": ["72"],
    "flat": ["69"],
    "casa": ["68", "74", "85"],
    "sobrado": ["47", "78", "73", "86", "48", "49"],
    "cobertura": ["83", "82"],
    "terreno": ["66", "79", "67", "165", "163"],
    "ponto comercial": ["64", "65"],
    "loja": ["76", "80", "65", "64"],
    "sala comercial": ["36", "35", "37", "38"],
    "galpao": ["57"],
    "galpão": ["57"],
    "kitnet": ["84"],
    "studio": ["48"],
    "kitchenette": ["49"],
    "casa condominio": ["85"],
    "sobrado condominio": ["73", "78"],
    "chacara": ["70"],
    "chácara": ["70"],
    "fazenda": ["71", "118"],
    "condominio": ["157", "160"],
    "condomínio": ["157", "160"],
    "garagem": ["61", "62", "60"],
    "salao": ["75"],
    "salão": ["75"],
    "salao comercial": ["75"],
    "salão comercial": ["75"],
}
