"""Integer color helpers. Channels are 0 to 255."""


def rgb_to_y(red: int, green: int, blue: int) -> int:
    return (299 * red + 587 * green + 114 * blue) // 1000
