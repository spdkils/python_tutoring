"""
This is a gcode re-arranger for the WAZER generated code.

This code was hacked together after a few days and HAS BUGS! User be warned.
The one KNOWN bug is the fact it will probably fail to deal with 3 deep nested parts.
(I haven't even tested that, I just fear it will blow up, or do something wrong.)


I created this because after 8 months of use, I have run across many cuts
that the default cut order limits the machines capabilities.

It has really three main use cases.
    1)  Preview the cut order VERY quickly and simply with a graphical interface. (SAFE)
    2)  Manually select a cut, and move it up or down in the list of cuts. (PROBABLY SAFE)
    3)  Recursivly order cuts with the enclosing cut always last. (LEAST SAFE)
    ** Always check the file out on another utiliy, no guarentees!

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

    points: tuple[float] = field(repr=False)
    g_code: str = field(repr=False)
    bbox: BBox
    used: bool = False
    children: "Part" = None

    def __hash__(self):
        return hash(self.g_code)


def read_file(filename: str) -> str:
    """Reads the provided file in UTF-8"""
    if Path(filename).is_file():
        with open(filename, "r", encoding="utf-8") as f_handle:
            return f_handle.read()
    return ""


def a_encloses_b(bbox_a: BBox, bbox_b: BBox) -> bool:
    """Checks if the bounding box a, encloses bounding box b"""
    return (
        bbox_a.min_x <= bbox_b.min_x
        and bbox_a.min_y <= bbox_b.min_y
        and bbox_a.max_x >= bbox_b.max_x
        and bbox_a.max_y >= bbox_b.max_y
    )


def parts_by_row(parts: list[Part]):
    """Takes an iterable of parts, and sorts them into psudo rows"""
    rows = []
    reverse_it = False
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
                reverse=reverse_it,
            )
        )
        reverse_it = not reverse_it
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
    regex = "(-?\\d+(?:\\.\\d+)?)"
    for match in re.finditer(
        BLOCK_START_REGEX + BLOCK_MIDDLE_REGEX + BLOCK_END_REGEX, gcode
    ):
        x_points = [float(x) for x in re.findall("X" + regex, match.group(0))]
        y_points = [float(y) for y in re.findall("Y" + regex, match.group(0))]
        part = Part(
            points=tuple((_x, _y) for _x, _y in zip(x_points, y_points)),
            g_code=match.group(0),
            bbox=BBox(min(x_points), min(y_points), max(x_points), max(y_points)),
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
        part.children = []
    for part1 in parts:
        for part2 in parts:
            if part1 != part2 and a_encloses_b(part1.bbox, part2.bbox):
                part1.children.append(part2)
                part1.used = part2.used = True
                blocks.setdefault(part1, []).append(part2)
    for part in parts:
        if not part.used:
            blocks.setdefault(part, [])
    sorted_blocks: dict[Part, list[Part]] = sorted(
        blocks, key=lambda x: x.bbox.max_y, reverse=True
    )
    rows = parts_by_row(sorted_blocks)
    new_order = []
    for row in rows:
        for part in row:
            extend = recursive_reorder(part)
            for item in extend:
                if item not in new_order:
                    new_order.append(item)
    return new_order


def recursive_reorder(part: Part):
    """Reorder parts children first into a list"""
    extension = []
    for subpart in part.children:
        extension.extend(recursive_reorder(subpart))
    return extension + [part]


def write_file(old_filename: Path, header: str, footer: str, parts: list[Part]):
    """Write the output file in the same folder as the input, adding a t_"""
    path = Path(old_filename)
    new_filename = path.parents[0] / f"t_{path.name}"
    with open(new_filename, mode="w", encoding="utf-8") as file:
        print(header, end="", file=file)
        for part in parts:
            print(part.g_code, end="", file=file)
        print(footer, end="", file=file)


def limit(num: int, min_allowed: int, max_allowed: int) -> int:
    """Set limits on a number"""
    if num < min_allowed:
        return min_allowed
    elif num > max_allowed:
        return max_allowed
    else:
        return num


def draw_parts(
    parts: list[Part], graph: sg.Graph, slider: sg.Slider, color="white smoke"
) -> dict[int, int]:
    """Draws the parts, and gives back the part mapping"""
    graph.erase()
    figures = {
        idx: graph.draw_lines(part.points, color=color)
        for idx, part in enumerate(parts)
    }
    slider.update(range=(0, len(figures)))
    slider.update(value=0)
    return figures


def list_files(folder: str):
    """Just list gcode files in supplied folder"""
    if Path(folder).is_dir():
        return [Path(x).name for x in glob.glob(folder + "/*.gcode")]


def create_window():
    "Create the window object/layout."
    sg.theme(choice(sg.theme_list()))

    # Graph objects for drawings sorta like local globals, smells bad, but I'm in a hurry.
    wazer_bed = sg.Graph(
        canvas_size=(CUT_WIDTH_MM, CUT_HEIGHT_MM),
        graph_bottom_left=(0, -CUT_HEIGHT_MM),
        graph_top_right=(CUT_WIDTH_MM, 0),
        float_values=True,
        background_color="black",
        enable_events=True,
        key="-GRAPH-",
    )
    list_box = sg.Listbox(
        values=[],
        key="-CUTS-",
        size=(20, 30),
        select_mode=sg.LISTBOX_SELECT_MODE_MULTIPLE,
        expand_y=True,
        enable_events=True,
    )
    slider = sg.Slider(
        (0, 0),
        orientation="horizontal",
        enable_events=True,
        key="-SLIDER-",
        expand_x=True,
    )
    # Layout of the gui
    left_col = [
        [
            sg.FolderBrowse("Select Folder"),
            sg.Input(
                "",
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
        [wazer_bed],
        [sg.Text(text="", key="-metadata-")],
    ]
    right_col = [
        [
            sg.Listbox(
                values=list_files("."),
                key="-FILES-",
                size=(30, 30),
                expand_y=True,
                enable_events=True,
            )
        ],
    ]
    layout = [[list_box, sg.Column(left_col), sg.Column(right_col)]]

    # GUI creation and execution loop.

    return sg.Window("", layout, use_custom_titlebar=True)


def main() -> int:
    """G_Code preview and re-order for WAM

    Returns:
        int: Exit code for system.
    """
    # pylint: disable=no-member

    window = create_window()

    # State of application
    # If this was object oriented these would be properites
    wazer_bed: sg.Graph = window["-GRAPH-"]
    cuts: sg.Listbox = window["-CUTS-"]
    files: sg.Listbox = window["-FILES-"]
    slider: sg.Slider = window["-SLIDER-"]
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
                files.update(values=list_files(folder))
            case ("-GRAPH-", {"-GRAPH-": pos}):
                idxs = ()
                for x_wiggle in (-1, 0, 1):
                    for y_wiggle in (-1, 0, 1):
                        idxs += wazer_bed.get_figures_at_location(
                            (pos[0] + x_wiggle, pos[1] + y_wiggle)
                        )
                locs = tuple(list(figure_mapping.values()).index(idx) for idx in idxs)
                if locs:
                    cuts.update(
                        set_to_index=locs + cuts.get_indexes(),
                        scroll_to_index=locs[0],
                    )
                for idx in locs:
                    wazer_bed.tk_canvas.itemconfig(figure_mapping[idx], fill="red")
            case ("-SLIDER-", {"-SLIDER-": pos}):
                for idx, fig in enumerate(cuts.get_list_values()):
                    if idx < pos:
                        wazer_bed.tk_canvas.itemconfig(figure_mapping[fig], fill="red")
                    else:
                        wazer_bed.tk_canvas.itemconfig(
                            figure_mapping[fig], fill="white smoke"
                        )
                selected = tuple(x for x in range(0, int(pos)))
                cuts.update(set_to_index=selected, scroll_to_index=int(pos) - 1)
            case ("-FILES-", values):
                if not values["-FILES-"]:
                    continue
                gcode = read_file(Path(values["Select Folder"]) / values["-FILES-"][0])
                header, footer, parts = parse_gcode(gcode)
                if not all((header, footer, parts)):
                    sg.popup("File did not parse correctly.")
                    continue
                window["-metadata-"].update(value="\n".join(header.splitlines()[1:9]))
                figure_mapping = draw_parts(parts, wazer_bed, slider)
                cuts.update(values=figure_mapping)
            case ("Re-Draw", values):
                if not all((header, footer, parts)):
                    continue
                figure_mapping = draw_parts(parts, wazer_bed, slider)
                cuts.update(values=figure_mapping)
            case ("Rearrange", *_):
                if not all((header, footer, parts)):
                    continue
                parts = reorder_parts(parts)
                figure_mapping = draw_parts(parts, wazer_bed, slider)
                cuts.update(values=figure_mapping)
            case ("--REDRAW--", {"--REDRAW--": num}):
                if not all((header, footer, parts)):
                    continue
                figure_mapping = draw_parts(parts, wazer_bed, slider)
                new_ind = [
                    limit(x + num, 0, len(cuts.get_list_values()) - 1)
                    for x in cuts.get_indexes()
                ]
                scroll = 0 if not new_ind else min(new_ind)
                cuts.update(
                    values=figure_mapping, set_to_index=new_ind, scroll_to_index=scroll
                )
                for fig in cuts.get_list_values():
                    wazer_bed.tk_canvas.itemconfig(
                        figure_mapping[fig],
                        fill="white smoke" if fig not in cuts.get() else "red",
                    )
            case ("-CUTS-", values):
                for fig in cuts.get_list_values():
                    wazer_bed.tk_canvas.itemconfig(
                        figure_mapping[fig],
                        fill="white smoke" if fig not in cuts.get() else "red",
                    )
            case (event, values) if event in ("Up", "Down"):
                val = -1 if event == "Up" else 1
                if all((figure_mapping, parts)):
                    for idx in cuts.get_indexes()[::-val]:
                        if 0 <= idx + val < len(parts):
                            parts[idx + val], parts[idx] = parts[idx], parts[idx + val]
                    window.write_event_value("--REDRAW--", val)
            case ("Save Copy", values):
                if all((values["-FILES-"], header, footer, parts)):
                    write_file(
                        Path(values["Select Folder"]) / values["-FILES-"][0],
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
