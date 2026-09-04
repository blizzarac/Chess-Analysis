import chess

from chess_analysis.analysis.book import LINES, lookup


def test_every_book_line_is_legal_and_unique():
    seen = set()
    for eco, name, line in LINES:
        board = chess.Board()
        for san in line.split():
            board.push_san(san)  # raises on an illegal move
        assert line not in seen, f"duplicate line {line}"
        seen.add(line)
        assert len(eco) == 3 and name


def test_lookup_names_and_deviation():
    info = lookup("e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 h3".split(), "white")
    assert info["name"] == "Ruy Lopez: Closed Variation"
    assert info["eco"] == "C84"
    assert info["deviation_ply"] == 11 and info["deviated_by"] == "player" and info["played"] == "h3"
    assert "Re1" in info["book_moves"]
    info = lookup("e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be3".split(), "black")
    assert info["name"] == "Sicilian Defense: Najdorf, English Attack"
    assert info["deviation_ply"] is None  # stayed in book for the whole prefix
    info = lookup("e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5 d5 exd5 Nxd5 Nxf7 Kxf7".split(), "black")
    assert info["name"].endswith("Fried Liver Attack")
    info = lookup("e4 a5".split(), "black")
    assert info["name"] == "King's Pawn Opening" or info["name"] is None or info["deviation_ply"] == 2
