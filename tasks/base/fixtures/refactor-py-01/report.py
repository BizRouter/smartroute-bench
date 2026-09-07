"""Report generation module.

Takes a header (list[str]) and rows (list[tuple]) and builds text reports
with different delimiters. Empty cells ("") are rendered as "-".
"""


def generate_csv_report(header, rows):
    lines = [",".join(header)]
    for row in rows:
        cells = []
        for c in row:
            s = str(c)
            if s == "":
                s = "-"
            cells.append(s)
        lines.append(",".join(cells))
    return "\n".join(lines) + "\n"


def generate_tsv_report(header, rows):
    lines = ["\t".join(header)]
    for row in rows:
        cells = []
        for c in row:
            s = str(c)
            if s == "":
                s = "-"
            cells.append(s)
        lines.append("\t".join(cells))
    return "\n".join(lines) + "\n"


def generate_pipe_report(header, rows):
    lines = [" | ".join(header)]
    for row in rows:
        cells = []
        for c in row:
            s = str(c)
            if s == "":
                s = "-"
            cells.append(s)
        lines.append(" | ".join(cells))
    return "\n".join(lines) + "\n"


# TODO: next quarter we also need a semicolon (;) separated report.
# With the current structure we'd have to copy-paste yet another function,
# so this needs to be reorganized so new delimiters are easy to add.
