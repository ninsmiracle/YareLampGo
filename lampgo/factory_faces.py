"""P4 factory faces. Firmware owns pixels; this catalog owns product semantics.
No user files are copied, deleted or replaced by these virtual assets.
"""

from pathlib import Path

P4_FACE_VERSION = "p4-face-v1"
P4_EYE_MAX_COUNT = 16
P4_EYE_BUDGET_BYTES = 4 * 1024 * 1024
P4_FS_BYTES = 0x5F0000
P4_RESERVED_BYTES = 512 * 1024
P4_STAGING_BYTES = 256 * 1024

FACES = [
    {
        "id": "p4_attentive",
        "mode": 34,
        "label": "静静听你说",
        "description": "目光轻移与浅笑，适合等待和专注",
        "fallback": "focused",
    },
    {
        "id": "p4_joy",
        "mode": 35,
        "label": "藏不住的开心",
        "description": "弯月笑眼与柔和上扬嘴角",
        "fallback": "smiley",
    },
    {
        "id": "p4_delight",
        "mode": 36,
        "label": "小小雀跃",
        "description": "笑眼轻跳，嘴角与星光同拍",
        "fallback": "star",
    },
    {"id": "p4_shy", "mode": 37, "label": "有一点害羞", "description": "视线侧下躲闪，腮红与小嘴", "fallback": "blush"},
    {"id": "p4_sad", "mode": 38, "label": "有点低落", "description": "低垂眼神与向下嘴角", "fallback": "sad"},
    {
        "id": "p4_ponder",
        "mode": 39,
        "label": "让我想一下",
        "description": "向上寻思，三点依次停顿",
        "fallback": "thinking",
    },
    {
        "id": "p4_sleepy",
        "mode": 40,
        "label": "呼吸入眠",
        "description": "闭眼缓缓起伏，小嘴随呼吸变化",
        "fallback": "sleep",
    },
    {
        "id": "p4_curious",
        "mode": 41,
        "label": "发现了什么",
        "description": "大小眼左右寻视，圆嘴保持好奇",
        "fallback": "question",
    },
    {
        "id": "p4_greet",
        "mode": 42,
        "label": "见到你真好",
        "description": "短暂眨眼，微笑与两侧红晕",
        "fallback": "wink",
    },
    {
        "id": "p4_agree",
        "mode": 43,
        "label": "嗯嗯，我同意",
        "description": "目光微点头，笑容旁亮起小勾",
        "fallback": "check",
    },
    {
        "id": "p4_refuse",
        "mode": 44,
        "label": "认真说不",
        "description": "轻轻摇眼神，平嘴表达拒绝",
        "fallback": "cross",
    },
    {"id": "p4_stretch", "mode": 45, "label": "舒展开来", "description": "目光上提，嘴角舒展", "fallback": "smiley"},
    {"id": "p4_lookup", "mode": 46, "label": "看向上方", "description": "向上凝视，嘴巴轻轻张开", "fallback": "up"},
    {
        "id": "p4_groove",
        "mode": 47,
        "label": "跟着节奏摇",
        "description": "笑眼和嘴角同拍摆动，边缘星光",
        "fallback": "music",
    },
]
BY_ID = {item["id"]: item for item in FACES}
# Removed from the P4 default shelf only; stable legacy IDs keep working.
P4_ARCHIVED_LED_IDS = {
    "red",
    "green",
    "blue",
    "white",
    "theater",
    "theaterred",
    "theatergreen",
    "theaterblue",
    "rainbowchase",
    "myu7gt",
    "focused",
    "smiley",
    "sad",
    "surprised",
    "blush",
    "angry",
    "thinking",
    "sleep",
    "helpless",
    "cool",
    "wink",
}
RECORDING_FACES = {
    "伸展": "p4_stretch",
    "低头鞠躬": "p4_greet",
    "向前看": "p4_attentive",
    "害羞": "p4_shy",
    "抬头看": "p4_lookup",
    "摇头": "p4_refuse",
    "摇头晃脑舞蹈": "p4_groove",
    "极度兴奋": "p4_delight",
    "沮丧": "p4_sad",
    "深度思考": "p4_ponder",
    "点头": "p4_agree",
    "环顾四周": "p4_curious",
    "睡觉": "p4_sleepy",
    "站立": "p4_attentive",
    "蹲下": "p4_sleepy",
}


def factory_eyes():
    return [
        {
            "eye_clip_id": x["id"],
            "storage_clip_id": x["id"],
            "label": x["label"],
            "platform": "esp32-p4",
            "source_kind": "factory",
            "fps": 20,
            "frame_count": 60,
            "duration_ms": 3000,
            "lcd": {"width": 320, "height": 172, "bytes": 0},
            "source": {"grid_rows": 6, "grid_cols": 10, "content_type": "image/png"},
            "default_led_effect_id": x["id"],
            "sync": {"status": "firmware_builtin"},
        }
        for x in FACES
    ]


def factory_effects():
    return [
        {
            "effect_id": x["id"],
            "label": x["label"],
            "description": x["description"],
            "mode": x["mode"],
            "source": "builtin",
            "platform": "esp32-p4",
            "role": "mouth",
            "animated": True,
            "default_playback": "loop",
            "duration_ms": 3000,
            "parameter_schema": {
                "color": {"type": "string", "format": "color"},
                "brightness": {"type": "integer", "minimum": 1, "maximum": 96, "default": 32},
                "intensity": {"type": "number", "minimum": 0.1, "maximum": 1.0, "default": 1.0},
            },
        }
        for x in FACES
    ]


def factory_presets():
    return [
        {
            "preset_id": x["id"],
            "label": x["label"],
            "description": x["description"],
            "eye_clip_id": x["id"],
            "led_effect_id": x["id"],
            "led_params": {},
            "playback": "loop",
            "duration_ms": 3000,
            "source": "factory",
            "platform": "esp32-p4",
        }
        for x in FACES
    ]


def eye_preview_path(eye_id):
    return Path(__file__).parent / "web" / "static" / "factory-faces" / f"{eye_id}.png"
