import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def make_sample(width: int) -> Image.Image:
    # Одну композицию можно отрисовать с разным разрешением для воспроизводимых замеров.
    height = width * 9 // 16
    scale = width / 960
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)

    def point(x, y):
        return round(x * scale), round(y * scale)

    def polygon(points, color):
        draw.polygon([point(*p) for p in points], fill=color)

    def line(points, color, thickness=1):
        draw.line([point(*p) for p in points], fill=color, width=max(1, round(thickness * scale)))

    for y in range(height):
        ratio = y / height
        color = tuple(round(a + (b - a) * ratio) for a, b in zip((174, 200, 198), (244, 209, 165)))
        draw.line((0, y, width, y), fill=color)

    draw.ellipse((*point(660, 72), *point(814, 226)), fill="#F5E3AD")
    polygon([(0, 272), (114, 177), (192, 144), (287, 207), (377, 177), (481, 275),
             (613, 213), (709, 268), (809, 204), (960, 266), (960, 540), (0, 540)], "#90A99D")
    polygon([(0, 320), (151, 241), (270, 216), (383, 310), (515, 286), (622, 324),
             (734, 273), (848, 309), (960, 269), (960, 540), (0, 540)], "#637F74")
    polygon([(0, 378), (147, 304), (304, 325), (441, 374), (546, 347), (690, 391),
             (848, 329), (960, 346), (960, 540), (0, 540)], "#355C59")
    polygon([(0, 428), (197, 377), (349, 403), (536, 453), (690, 419), (960, 394),
             (960, 540), (0, 540)], "#294849")
    for index in range(27):
        y = 407 + 5 * index
        x = (index * 65 + 47) % 475
        length = 78 + index * 6
        line([(x, y), (x + length, y)], ("#507776", "#799991", "#3B615F")[index % 3])
        line([(x + 455, y + 2), (min(x + 530 + length, 960), y + 2)], "#507776")

    # Каменный мост, тени внутри пролётов и тонкие швы дают разные перепады яркости.
    mask = Image.new("L", image.size)
    arches = ImageDraw.Draw(mask)
    arches.rectangle((*point(102, 289), *point(872, 441)), fill=255)
    for x in range(124, 796, 112):
        arches.rounded_rectangle((*point(x, 337), *point(x + 82, 471)),
                                 radius=round(41 * scale), fill=0)
    masonry = Image.new("RGB", image.size, "#D8A477")
    stones = ImageDraw.Draw(masonry)
    for y in range(301, 454, 12):
        stones.line((*point(102, y), *point(872, y)), fill="#AD785C", width=max(1, round(scale)))
        for x in range(104 + (y % 24), 872, 28):
            stones.line((*point(x, y), *point(x, y + 12)), fill="#C18967", width=max(1, round(scale)))
    image.paste(masonry, (0, 0), mask)
    line([(96, 290), (880, 290)], "#EED4A9", 8)
    line([(102, 303), (872, 303)], "#794F40", 3)
    line([(102, 281), (872, 281)], "#923E39", 2)
    for x in range(106, 873, 20):
        line([(x, 281), (x, 290)], "#923E39", 1)
    return image


def main():
    parser = argparse.ArgumentParser(description="Создать тестовый BMP RGB24")
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=1920)
    args = parser.parse_args()
    if not 64 <= args.width <= 6400:
        parser.error("Допустимая ширина образца — от 64 до 6400 пикселей")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    make_sample(args.width).save(args.output, format="BMP")


if __name__ == "__main__":
    main()
