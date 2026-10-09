from dataclasses import dataclass

@dataclass
class Theme:
    bg_color: tuple = (255, 255, 255)
    title_color: tuple = (20, 20, 20)
    text_color: tuple = (60, 60, 60)
    accent_color: tuple = (0, 112, 192)
    title_font: str = "Calibri"
    body_font: str = "Calibri"
    title_size: int = 32
    body_size: int = 18

THEMES = {"default": Theme()}