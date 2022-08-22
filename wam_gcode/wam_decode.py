"""
This is a gcode re-arranger for the WAZER generated code.

This code was hacked together after a few days and HAS BUGS! User be warned.
The one KNOWN bug is the fact it will probably fail to deal with 3 deep nested parts.
(I haven't even tested that, I just fear it will blow up, or do something wrong.)


I created this because after 8 months of use, I have run across many cuts that not being able to direct the cut order limits the machines capabilities.

It has really three main use cases.
    1)  Just preview the cut order VERY quickly and simply with a graphical interface. (Much easier for quick checks than NCViewer) (SAFE)
    2)  It will allow you to manually select a cut, and move it up or down in the list of cuts, then let you save a COPY of the file with that change. (PROBABLY SAFE)
    3)  It will find cuts that enclose other cuts, and ensure to cut them as a group, with the enclosing cut always last. (LEAST SAFE)
    ** Always check the file out on another utiliy, no guarentees!

My hope is I don't have to maintain this thing for long, I hope that Wazer will add options to select differing cut orders, as well a
cut order modification gui to their web tool. (I almost created this as a stand alone web app hosted on github using pyscript, and I may still do that
just to learn how to do it.)

"""

import glob
import re
import sys
from collections import namedtuple
from dataclasses import dataclass, field
from pathlib import Path
from random import choice

import PySimpleGUI as sg

# GCode constants for WAM gcode files
# These constants were all aquired from https://wam.wazer.com/wazercam/wazercam.min.js
# WAZER can change them when ever they want, so there be dragons.
HEADER_REGEX = "(.*\n)+?M1412 .*\n"
BLOCK_START_REGEX = "G0 X-?(\\d+(?:\\.\\d+)?) Y-?(\\d+(?:.\\d+)?)\n"
BLOCK_MIDDLE_REGEX = "(.*\n)+?"
BLOCK_END_REGEX = "G4 S1.\nM5\nG4 S1.\n"
FOOTER_REGEX = "M1413 .*(.*\n)+"
CUT_WIDTH_MM = 457
CUT_HEIGHT_MM = 304

BBox = namedtuple("BBox", "min_x, min_y, max_x, max_y")


@dataclass
class Part:
    """Minimum data required to easily deal with g-code sections."""

    points: tuple[float]
    g_code: str = field(repr=False)
    bbox: BBox
    used: bool = False
    parent: "Part" = None  # Used to deal with a small part that may get inside the rectangular bbox of another part.

    def __hash__(self):
        return hash(self.g_code)


def read_file(filename: str) -> str:
    """Reads the provided file in UTF-8"""
    if Path(filename).is_file():
        with open(filename, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def a_encloses_b(a: BBox, b: BBox) -> bool:
    """Checks if the bounding box a, encloses bounding box b"""
    return (
        a.min_x < b.min_x
        and a.min_y < b.min_y
        and a.max_x > b.max_x
        and a.max_y > b.max_y
    )


def parts_by_row(parts: list[Part]):
    """Takes an iterable of parts, and sorts them into psudo rows"""
    rows = []
    reversed = False
    new_parts = sorted(parts, key=lambda x: x.bbox.max_y, reverse=True)
    while new_parts:
        rows.append(
            sorted(
                [
                    x
                    for x in new_parts
                    if new_parts[0].bbox.min_y < x.bbox.max_y or x == new_parts[0]
                ],
                key=lambda x: x.bbox.min_x,
                reverse=reversed,
            )
        )
        reversed = not reversed
        [new_parts.remove(x) for x in rows[-1]]

    return rows


def parse_gcode(gcode: str) -> tuple[str, str, list[Part]]:
    """Take WAZER g-code and break it into sections

    See the code in https://wam.wazer.com/wazercam/wazercam.min.js
    """
    header = re.match(HEADER_REGEX, gcode)
    footer = re.search(FOOTER_REGEX, gcode)
    parts: list[Part] = []

    # hacked this up three times I should probably deal with the x/y as pairs in the regex
    # Meh...
    for match in re.finditer(
        BLOCK_START_REGEX + BLOCK_MIDDLE_REGEX + BLOCK_END_REGEX, gcode
    ):
        x = [float(x) for x in re.findall("X(-?\\d+(?:\\.\\d+)?)", match.group(0))]
        y = [float(y) for y in re.findall("Y(-?\\d+(?:\\.\\d+)?)", match.group(0))]
        part = Part(
            points=tuple((_x, _y) for _x, _y in zip(x, y)),
            g_code=match.group(0),
            bbox=BBox(min(x), min(y), max(x), max(y)),
        )
        parts.append(part)
    if not all((header, footer, parts)):
        return None, None, None
    return header.group(0), footer.group(0), parts


def reorder_parts(parts: list[Part]) -> list[Part]:
    """Reorder the parts in a 'sane' fasion.

    Currently it simply takes each section of gcode (G0 bookends)
    Determines if there are any bounding boxes that wholely fall
    inside the part it's looking at, and if there is... It considers
    it a child part. (This will BUG out if you have a part in a part in a part!!!)

    Once it knows parts that have parts inside them, it orders them by some psudo rows
    then children parts are drawn first, then the part they are contained in.
    (TODO: maybe make this recursive to deal with the nesting issue.)
    """

    # I'm sure I could do this recursivly and more elegantly to deal with deep nesting
    # That is a problem for later.
    blocks = {}
    for part in parts:
        part.used = False
        part.parent = None
    for part1 in parts:
        for part2 in parts:
            if part1 != part2 and a_encloses_b(part1.bbox, part2.bbox):
                if part2.parent is None:
                    part2.parent = part1
                    part1.used = part2.used = True
                    blocks.setdefault(part1, []).append(part2)
                elif a_encloses_b(part1.bbox, part2.parent.bbox):
                    part2.parent.parent = part1
                    part1.used = part2.parent.used = True
                    blocks.setdefault(part1, []).append(part2.parent)
    for part in parts:
        if not part.used:
            blocks.setdefault(part, [])
    sorted_blocks: dict[Part, list[Part]] = sorted(
        blocks, key=lambda x: x.bbox.max_y, reverse=True
    )
    rows = parts_by_row(sorted_blocks)
    new_order = []
    for row in rows:
        for item in row:
            for child in blocks[item]:
                new_order.append(child)
            new_order.append(item)
    return new_order


def write_file(old_filename: Path, header: str, footer: str, parts: list[Part]):
    """Write the output file in the same folder as the input, adding a t_"""
    path = Path(old_filename)
    new_filename = path.parents[0] / f"t_{path.name}"
    with open(new_filename, mode="w", encoding="utf-8") as f:
        print(header, end="", file=f)
        for part in parts:
            print(part.g_code, end="", file=f)
        print(footer, end="", file=f)


def limit(num: int, min_allowed: int, max_allowed: int) -> int:
    """Set limits on a number"""
    if num < min_allowed:
        return min_allowed
    elif num > max_allowed:
        return max_allowed
    else:
        return num


def draw_parts(
    parts: list[Part], graph: sg.Graph, color="white smoke"
) -> dict[int, int]:
    """Draws the parts, and gives back the part mapping"""
    graph.erase()
    return {
        idx: graph.draw_lines(part.points, color=color)
        for idx, part in enumerate(parts)
    }


def list_files(folder: str):
    if Path(folder).is_dir():
        return [Path(x).name for x in glob.glob(folder + "/*.gcode")]


def main() -> int:
    """G_Code preview and re-order for WAM

    Returns:
        int: Exit code for system.
    """
    sg.theme(choice(sg.theme_list()))
    DRAW_COLOR = sg.theme_text_color()

    # Graph objects for drawings sorta like local globals, smells bad, but I'm in a hurry.
    g1 = sg.Graph(
        canvas_size=(CUT_WIDTH_MM, CUT_HEIGHT_MM),
        graph_bottom_left=(0, -CUT_HEIGHT_MM),
        graph_top_right=(CUT_WIDTH_MM, 0),
        float_values=True,
        background_color="black",
        enable_events=True,
        key="-raw_image-",
    )
    list_box = []
    lb = sg.Listbox(
        values=list_box,
        key="Parts",
        size=(20, 30),
        select_mode=sg.LISTBOX_SELECT_MODE_MULTIPLE,
        expand_y=True,
        enable_events=True,
    )
    slider = sg.Slider(
        (0, 0),
        orientation="horizontal",
        enable_events=True,
        key="-animate-",
        expand_x=True,
    )
    # Layout of the gui
    left_col = [
        [
            sg.FolderBrowse("Select Folder"),
            sg.Input(
                ".",
                readonly=True,
                enable_events=True,
                key="-foldername-",
                text_color="black",
            ),
        ],
        [
            sg.Button("Re-Draw"),
            sg.Button("Rearrange"),
            sg.Button("Save Copy"),
            sg.Button("Exit"),
        ],
        [sg.Button("Up"), sg.Button("Down")],
        [slider],
        [g1],
    ]
    right_col = [
        [
            sg.Listbox(
                values=[list_files(".")],
                key="Files",
                size=(30, 30),
                expand_y=True,
                enable_events=True,
            )
        ],
    ]
    layout = [[lb, sg.Column(left_col), sg.Column(right_col)]]

    # GUI creation and execution loop.

    window = sg.Window("", layout, use_custom_titlebar=True)

    # State of application
    # If this was object oriented these would be properites
    gcode = ""
    header = footer = parts = None
    figure_mapping = {}

    # Main loop that responsed to events, and all that jazz
    while True:
        # time to use Structural Pattern Matching (SPM)
        # okay this got out of hand, lots of repitition in here
        # refactor and functionize this soon(tm)
        match window.read():
            case (sg.WIN_CLOSED | "Exit", *_):
                window.close()
                return 0
            case ("-foldername-", {"Select Folder": folder}):
                window["Files"].update(values=list_files(folder))

            case ("-raw_image-", {"-raw_image-": pos}):
                idxs = ()
                for x in (-1, 0, 1):
                    for y in (-1, 0, 1):
                        idxs += g1.get_figures_at_location((pos[0] + x, pos[1] + y))
                locs = tuple(list(figure_mapping.values()).index(idx) for idx in idxs)
                if locs:
                    window["Parts"].update(
                        set_to_index=locs + lb.get_indexes(),
                        scroll_to_index=locs[0],
                    )
                for idx in locs:
                    g1.tk_canvas.itemconfig(figure_mapping[idx], fill="red")
            case ("-animate-", {"-animate-": pos}):
                for idx, fig in enumerate(window["Parts"].Values):
                    if idx < pos:
                        g1.tk_canvas.itemconfig(figure_mapping[fig], fill="red")
                    else:
                        g1.tk_canvas.itemconfig(figure_mapping[fig], fill="white smoke")
                selected = tuple(x for x in range(0, int(pos)))
                lb.update(set_to_index=selected, scroll_to_index=int(pos) - 1)
            case ("Files", values):
                if not values["Files"]:
                    continue
                gcode = read_file(Path(values["Select Folder"]) / values["Files"][0])
                header, footer, parts = parse_gcode(gcode)
                if not all((header, footer, parts)):
                    sg.popup("File did not parse correctly.")
                    continue
                figure_mapping = draw_parts(parts, g1)
                slider.update(range=(0, len(figure_mapping)))
                slider.update(value=0)
                lb.update(values=figure_mapping)
            case ("Re-Draw", values):
                if not all((header, footer, parts)):
                    continue
                figure_mapping = draw_parts(parts, g1)
                slider.update(value=0)
                lb.update(values=figure_mapping)
            case ("Rearrange", *_):
                if not all((header, footer, parts)):
                    continue
                parts = reorder_parts(parts)
                figure_mapping = draw_parts(parts, g1)
                slider.update(value=0)
                lb.update(values=figure_mapping)
            case ("--REDRAW--", {"--REDRAW--": num}):
                if not all((header, footer, parts)):
                    continue
                figure_mapping = draw_parts(parts, g1)
                new_ind = [
                    limit(x + num, 0, len(lb.get_list_values()) - 1)
                    for x in lb.get_indexes()
                ]
                scroll = 0 if not new_ind else new_ind[0]
                lb.update(
                    values=figure_mapping, set_to_index=new_ind, scroll_to_index=scroll
                )
                for fig in window["Parts"].Values:
                    g1.tk_canvas.itemconfig(figure_mapping[fig], fill="white smoke")
                for fig in new_ind:
                    g1.tk_canvas.itemconfig(figure_mapping[fig], fill="red")
            case ("Parts", values):
                for fig in window["Parts"].Values:
                    g1.tk_canvas.itemconfig(figure_mapping[fig], fill="white smoke")
                for fig in values["Parts"]:
                    g1.tk_canvas.itemconfig(figure_mapping[fig], fill="red")
            case (event, values) if event in ("Up", "Down"):
                val = -1 if event == "Up" else 1
                if all((figure_mapping, parts)):
                    for idx in lb.get_indexes()[::-val]:
                        if 0 <= idx + val < len(parts):
                            parts[idx + val], parts[idx] = parts[idx], parts[idx + val]
                    window.write_event_value("--REDRAW--", val)
            case ("Save Copy", values):
                if all((values["Files"], header, footer, parts)):
                    write_file(
                        Path(values["Select Folder"]) / values["Files"][0],
                        header,
                        footer,
                        parts,
                    )
            case (*args,):
                print("No clue what just happend? You added an event without a case?")
                print(args)
                window.close()
                return 1


if __name__ == "__main__":
    sys.exit(main())
