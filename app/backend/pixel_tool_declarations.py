"""Shared OpenAPI-style function declarations for Pixel (REST + Live)."""

# Keep in sync with firmware faceAnimMode mapping in bots/Pixel/src/main.cpp.
FACE_ANIMATION_NAMES = (
    "speaking",
    "happy",
    "mad",
    "sad",
    "surprised",
    "sleepy",
    "thinking",
    "confused",
    "excited",
    "love",
)

FACE_ANIMATION_FUNCTION_DECLARATION = {
    "name": "face_animation",
    "description": (
        "Animates Pixel's face on the round display for conversational or emotional states only. "
        "Pass only which face to show: one of the listed animation names."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "animation": {
                "type": "string",
                "enum": list(FACE_ANIMATION_NAMES),
                "description": "Which face animation to display.",
            },
        },
        "required": ["animation"],
    },
}
