"""리포트 생성 모듈.

header(list[str])와 rows(list[tuple])를 받아 구분자별 텍스트 리포트를 만든다.
빈 셀("")은 "-" 로 표기한다.
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


# TODO: 다음 분기에 세미콜론(;) 구분 리포트도 추가해야 한다.
# 지금 구조로는 함수를 또 복붙해야 하니, 구분자를 쉽게 추가할 수 있게 정리 필요.
