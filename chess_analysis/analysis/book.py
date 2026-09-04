"""A compact opening book: ECO code, name and the defining move sequence.

Used to (a) give every game a stable opening name based on the moves actually played rather
than chess.com's label and (b) find the first move where a game left known theory and who
played it. Lines are short on purpose: this is about recognising the opening, not about
memorising deep theory. `tests/test_book.py` checks every line is legal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

LINES: list[tuple[str, str, str]] = [
    # --- 1.e4 e5 --------------------------------------------------------------------------
    ("C20", "King's Pawn Game", "e4 e5"),
    ("C40", "King's Knight Opening", "e4 e5 Nf3"),
    ("C41", "Philidor Defense", "e4 e5 Nf3 d6"),
    ("C42", "Petrov's Defense", "e4 e5 Nf3 Nf6"),
    ("C43", "Petrov's Defense: Steinitz Attack", "e4 e5 Nf3 Nf6 d4"),
    ("C44", "Scotch Game", "e4 e5 Nf3 Nc6 d4"),
    ("C45", "Scotch Game: Main Line", "e4 e5 Nf3 Nc6 d4 exd4 Nxd4"),
    ("C44", "Ponziani Opening", "e4 e5 Nf3 Nc6 c3"),
    ("C46", "Three Knights Opening", "e4 e5 Nf3 Nc6 Nc3"),
    ("C47", "Four Knights Game", "e4 e5 Nf3 Nc6 Nc3 Nf6"),
    ("C48", "Four Knights Game: Spanish Variation", "e4 e5 Nf3 Nc6 Nc3 Nf6 Bb5"),
    ("C47", "Four Knights Game: Scotch Variation", "e4 e5 Nf3 Nc6 Nc3 Nf6 d4"),
    ("C50", "Italian Game", "e4 e5 Nf3 Nc6 Bc4"),
    ("C50", "Italian Game: Giuoco Piano", "e4 e5 Nf3 Nc6 Bc4 Bc5"),
    ("C53", "Italian Game: Giuoco Piano, Main Line", "e4 e5 Nf3 Nc6 Bc4 Bc5 c3"),
    ("C54", "Italian Game: Giuoco Pianissimo", "e4 e5 Nf3 Nc6 Bc4 Bc5 d3"),
    ("C51", "Italian Game: Evans Gambit", "e4 e5 Nf3 Nc6 Bc4 Bc5 b4"),
    ("C50", "Italian Game: Hungarian Defense", "e4 e5 Nf3 Nc6 Bc4 Be7"),
    ("C55", "Italian Game: Two Knights Defense", "e4 e5 Nf3 Nc6 Bc4 Nf6"),
    ("C57", "Italian Game: Two Knights Defense, Knight Attack", "e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5"),
    ("C58", "Italian Game: Two Knights Defense, Polerio Defense", "e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5 d5 exd5 Na5"),
    ("C57", "Italian Game: Two Knights Defense, Fried Liver Attack", "e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5 d5 exd5 Nxd5 Nxf7"),
    ("C57", "Italian Game: Two Knights Defense, Traxler Counterattack", "e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5 Bc5"),
    ("C55", "Italian Game: Two Knights Defense, Modern Bishop's Opening", "e4 e5 Nf3 Nc6 Bc4 Nf6 d3"),
    ("C60", "Ruy Lopez", "e4 e5 Nf3 Nc6 Bb5"),
    ("C65", "Ruy Lopez: Berlin Defense", "e4 e5 Nf3 Nc6 Bb5 Nf6"),
    ("C67", "Ruy Lopez: Berlin Defense, Open Variation", "e4 e5 Nf3 Nc6 Bb5 Nf6 O-O Nxe4"),
    ("C64", "Ruy Lopez: Classical Variation", "e4 e5 Nf3 Nc6 Bb5 Bc5"),
    ("C63", "Ruy Lopez: Schliemann Defense", "e4 e5 Nf3 Nc6 Bb5 f5"),
    ("C62", "Ruy Lopez: Steinitz Defense", "e4 e5 Nf3 Nc6 Bb5 d6"),
    ("C68", "Ruy Lopez: Exchange Variation", "e4 e5 Nf3 Nc6 Bb5 a6 Bxc6"),
    ("C70", "Ruy Lopez: Morphy Defense", "e4 e5 Nf3 Nc6 Bb5 a6 Ba4"),
    ("C77", "Ruy Lopez: Morphy Defense, Main Line", "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6"),
    ("C80", "Ruy Lopez: Open Variation", "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Nxe4"),
    ("C84", "Ruy Lopez: Closed Variation", "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7"),
    ("C88", "Ruy Lopez: Closed Variation, Main Line", "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 O-O"),
    ("C89", "Ruy Lopez: Marshall Attack", "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 O-O c3 d5"),
    ("C30", "King's Gambit", "e4 e5 f4"),
    ("C33", "King's Gambit Accepted", "e4 e5 f4 exf4"),
    ("C34", "King's Gambit Accepted: King's Knight Gambit", "e4 e5 f4 exf4 Nf3"),
    ("C30", "King's Gambit Declined: Classical", "e4 e5 f4 Bc5"),
    ("C31", "King's Gambit Declined: Falkbeer Countergambit", "e4 e5 f4 d5"),
    ("C25", "Vienna Game", "e4 e5 Nc3"),
    ("C26", "Vienna Game: Falkbeer Variation", "e4 e5 Nc3 Nf6"),
    ("C29", "Vienna Game: Vienna Gambit", "e4 e5 Nc3 Nf6 f4"),
    ("C25", "Vienna Game: Max Lange Defense", "e4 e5 Nc3 Nc6"),
    ("C23", "Bishop's Opening", "e4 e5 Bc4"),
    ("C24", "Bishop's Opening: Berlin Defense", "e4 e5 Bc4 Nf6"),
    ("C21", "Center Game", "e4 e5 d4 exd4"),
    ("C21", "Danish Gambit", "e4 e5 d4 exd4 c3"),
    ("C22", "Center Game: Paulsen Attack", "e4 e5 d4 exd4 Qxd4 Nc6 Qe3"),
    ("C20", "King's Pawn Game: Alapin Opening", "e4 e5 Ne2"),
    ("C20", "King's Pawn Game: Napoleon Attack", "e4 e5 Qf3"),
    ("C20", "King's Pawn Game: Wayward Queen Attack", "e4 e5 Qh5"),
    ("C40", "Latvian Gambit", "e4 e5 Nf3 f5"),
    ("C40", "Elephant Gambit", "e4 e5 Nf3 d5"),
    ("C40", "Damiano Defense", "e4 e5 Nf3 f6"),
    # --- Sicilian -------------------------------------------------------------------------
    ("B20", "Sicilian Defense", "e4 c5"),
    ("B21", "Sicilian Defense: Smith-Morra Gambit", "e4 c5 d4 cxd4 c3"),
    ("B22", "Sicilian Defense: Alapin Variation", "e4 c5 c3"),
    ("B23", "Sicilian Defense: Closed", "e4 c5 Nc3"),
    ("B23", "Sicilian Defense: Grand Prix Attack", "e4 c5 Nc3 Nc6 f4"),
    ("B21", "Sicilian Defense: McDonnell Attack", "e4 c5 f4"),
    ("B20", "Sicilian Defense: Bowdler Attack", "e4 c5 Bc4"),
    ("B27", "Sicilian Defense: Hyperaccelerated Dragon", "e4 c5 Nf3 g6"),
    ("B30", "Sicilian Defense: Old Sicilian", "e4 c5 Nf3 Nc6"),
    ("B30", "Sicilian Defense: Rossolimo Variation", "e4 c5 Nf3 Nc6 Bb5"),
    ("B32", "Sicilian Defense: Open, Nc6", "e4 c5 Nf3 Nc6 d4 cxd4 Nxd4"),
    ("B33", "Sicilian Defense: Sveshnikov Variation", "e4 c5 Nf3 Nc6 d4 cxd4 Nxd4 Nf6 Nc3 e5"),
    ("B34", "Sicilian Defense: Accelerated Dragon", "e4 c5 Nf3 Nc6 d4 cxd4 Nxd4 g6"),
    ("B40", "Sicilian Defense: French Variation", "e4 c5 Nf3 e6"),
    ("B41", "Sicilian Defense: Kan Variation", "e4 c5 Nf3 e6 d4 cxd4 Nxd4 a6"),
    ("B44", "Sicilian Defense: Taimanov Variation", "e4 c5 Nf3 e6 d4 cxd4 Nxd4 Nc6"),
    ("B50", "Sicilian Defense: Modern Variations", "e4 c5 Nf3 d6"),
    ("B51", "Sicilian Defense: Moscow Variation", "e4 c5 Nf3 d6 Bb5+"),
    ("B54", "Sicilian Defense: Open, d6", "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6"),
    ("B56", "Sicilian Defense: Classical Variation", "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 Nc6"),
    ("B70", "Sicilian Defense: Dragon Variation", "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 g6"),
    ("B76", "Sicilian Defense: Dragon, Yugoslav Attack", "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 g6 Be3 Bg7 f3"),
    ("B80", "Sicilian Defense: Scheveningen Variation", "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 e6"),
    ("B90", "Sicilian Defense: Najdorf Variation", "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6"),
    ("B90", "Sicilian Defense: Najdorf, English Attack", "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be3"),
    ("B92", "Sicilian Defense: Najdorf, Opocensky Variation", "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be2"),
    ("B96", "Sicilian Defense: Najdorf, 6.Bg5", "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Bg5"),
    ("B27", "Sicilian Defense: Nimzowitsch Variation", "e4 c5 Nf3 Nf6"),
    # --- French, Caro-Kann and other 1.e4 defences ----------------------------------------
    ("C00", "French Defense", "e4 e6"),
    ("C00", "French Defense: King's Indian Attack", "e4 e6 d3"),
    ("C01", "French Defense: Exchange Variation", "e4 e6 d4 d5 exd5"),
    ("C02", "French Defense: Advance Variation", "e4 e6 d4 d5 e5"),
    ("C03", "French Defense: Tarrasch Variation", "e4 e6 d4 d5 Nd2"),
    ("C10", "French Defense: Rubinstein Variation", "e4 e6 d4 d5 Nc3 dxe4"),
    ("C11", "French Defense: Classical Variation", "e4 e6 d4 d5 Nc3 Nf6"),
    ("C15", "French Defense: Winawer Variation", "e4 e6 d4 d5 Nc3 Bb4"),
    ("B10", "Caro-Kann Defense", "e4 c6"),
    ("B12", "Caro-Kann Defense: Advance Variation", "e4 c6 d4 d5 e5"),
    ("B12", "Caro-Kann Defense: Fantasy Variation", "e4 c6 d4 d5 f3"),
    ("B13", "Caro-Kann Defense: Exchange Variation", "e4 c6 d4 d5 exd5 cxd5"),
    ("B13", "Caro-Kann Defense: Panov Attack", "e4 c6 d4 d5 exd5 cxd5 c4"),
    ("B15", "Caro-Kann Defense: Main Line", "e4 c6 d4 d5 Nc3 dxe4 Nxe4"),
    ("B18", "Caro-Kann Defense: Classical Variation", "e4 c6 d4 d5 Nc3 dxe4 Nxe4 Bf5"),
    ("B17", "Caro-Kann Defense: Karpov Variation", "e4 c6 d4 d5 Nc3 dxe4 Nxe4 Nd7"),
    ("B11", "Caro-Kann Defense: Two Knights Attack", "e4 c6 Nc3 d5 Nf3"),
    ("B01", "Scandinavian Defense", "e4 d5"),
    ("B01", "Scandinavian Defense: Mieses-Kotroc Variation", "e4 d5 exd5 Qxd5"),
    ("B01", "Scandinavian Defense: Modern Variation", "e4 d5 exd5 Nf6"),
    ("B02", "Alekhine Defense", "e4 Nf6"),
    ("B04", "Alekhine Defense: Modern Variation", "e4 Nf6 e5 Nd5 d4 d6 Nf3"),
    ("B06", "Modern Defense", "e4 g6"),
    ("B07", "Pirc Defense", "e4 d6 d4 Nf6 Nc3"),
    ("B00", "Nimzowitsch Defense", "e4 Nc6"),
    ("B00", "Owen's Defense", "e4 b6"),
    # --- 1.d4 -----------------------------------------------------------------------------
    ("D00", "Queen's Pawn Game", "d4 d5"),
    ("D02", "Queen's Pawn Game: London System", "d4 d5 Nf3 Nf6 Bf4"),
    ("D02", "Queen's Pawn Game: Accelerated London System", "d4 d5 Bf4"),
    ("D02", "Queen's Pawn Game: Zukertort Variation", "d4 d5 Nf3 Nf6 e3"),
    ("D04", "Queen's Pawn Game: Colle System", "d4 d5 Nf3 Nf6 e3 e6 Bd3"),
    ("D00", "Queen's Pawn Game: Blackmar-Diemer Gambit", "d4 d5 e4"),
    ("D01", "Richter-Veresov Attack", "d4 d5 Nc3 Nf6 Bg5"),
    ("D00", "Queen's Pawn Game: Stonewall Attack", "d4 d5 e3 Nf6 Bd3"),
    ("D06", "Queen's Gambit", "d4 d5 c4"),
    ("D07", "Queen's Gambit Declined: Chigorin Defense", "d4 d5 c4 Nc6"),
    ("D08", "Queen's Gambit Declined: Albin Countergambit", "d4 d5 c4 e5"),
    ("D10", "Slav Defense", "d4 d5 c4 c6"),
    ("D11", "Slav Defense: Modern Line", "d4 d5 c4 c6 Nf3"),
    ("D15", "Slav Defense: Three Knights Variation", "d4 d5 c4 c6 Nf3 Nf6 Nc3"),
    ("D17", "Slav Defense: Czech Variation", "d4 d5 c4 c6 Nf3 Nf6 Nc3 dxc4 a4 Bf5"),
    ("D20", "Queen's Gambit Accepted", "d4 d5 c4 dxc4"),
    ("D30", "Queen's Gambit Declined", "d4 d5 c4 e6"),
    ("D31", "Semi-Slav Defense", "d4 d5 c4 e6 Nc3 c6"),
    ("D35", "Queen's Gambit Declined: Exchange Variation", "d4 d5 c4 e6 Nc3 Nf6 cxd5 exd5"),
    ("D37", "Queen's Gambit Declined: Harrwitz Attack", "d4 d5 c4 e6 Nc3 Nf6 Nf3 Be7 Bf4"),
    ("D43", "Semi-Slav Defense: Main Line", "d4 d5 c4 c6 Nf3 Nf6 Nc3 e6"),
    ("D47", "Semi-Slav Defense: Meran Variation", "d4 d5 c4 c6 Nf3 Nf6 Nc3 e6 e3 Nbd7 Bd3 dxc4"),
    ("D53", "Queen's Gambit Declined: Orthodox Defense", "d4 d5 c4 e6 Nc3 Nf6 Bg5"),
    ("D38", "Queen's Gambit Declined: Ragozin Defense", "d4 d5 c4 e6 Nc3 Nf6 Nf3 Bb4"),
    ("A45", "Indian Game", "d4 Nf6"),
    ("A45", "Trompowsky Attack", "d4 Nf6 Bg5"),
    ("A45", "Indian Game: London System", "d4 Nf6 Bf4"),
    ("A46", "Indian Game: Knights Variation", "d4 Nf6 Nf3"),
    ("A46", "Torre Attack", "d4 Nf6 Nf3 e6 Bg5"),
    ("A48", "London System vs King's Indian", "d4 Nf6 Nf3 g6 Bf4"),
    ("A50", "Indian Game: Normal Variation", "d4 Nf6 c4"),
    ("A56", "Benoni Defense", "d4 Nf6 c4 c5"),
    ("A57", "Benko Gambit", "d4 Nf6 c4 c5 d5 b5"),
    ("A60", "Benoni Defense: Modern Variation", "d4 Nf6 c4 c5 d5 e6"),
    ("A51", "Budapest Gambit", "d4 Nf6 c4 e5"),
    ("E60", "King's Indian Defense", "d4 Nf6 c4 g6"),
    ("E61", "King's Indian Defense: Normal Variation", "d4 Nf6 c4 g6 Nc3 Bg7"),
    ("E70", "King's Indian Defense: Normal Variation, e4", "d4 Nf6 c4 g6 Nc3 Bg7 e4 d6"),
    ("E90", "King's Indian Defense: Classical Variation", "d4 Nf6 c4 g6 Nc3 Bg7 e4 d6 Nf3 O-O Be2 e5"),
    ("E97", "King's Indian Defense: Mar del Plata Variation", "d4 Nf6 c4 g6 Nc3 Bg7 e4 d6 Nf3 O-O Be2 e5 O-O Nc6 d5 Ne7"),
    ("E76", "King's Indian Defense: Four Pawns Attack", "d4 Nf6 c4 g6 Nc3 Bg7 e4 d6 f4"),
    ("E80", "King's Indian Defense: Sämisch Variation", "d4 Nf6 c4 g6 Nc3 Bg7 e4 d6 f3"),
    ("E62", "King's Indian Defense: Fianchetto Variation", "d4 Nf6 c4 g6 Nf3 Bg7 g3"),
    ("D70", "Grünfeld Defense", "d4 Nf6 c4 g6 Nc3 d5"),
    ("D85", "Grünfeld Defense: Exchange Variation", "d4 Nf6 c4 g6 Nc3 d5 cxd5 Nxd5 e4"),
    ("E20", "Nimzo-Indian Defense", "d4 Nf6 c4 e6 Nc3 Bb4"),
    ("E32", "Nimzo-Indian Defense: Classical Variation", "d4 Nf6 c4 e6 Nc3 Bb4 Qc2"),
    ("E40", "Nimzo-Indian Defense: Rubinstein Variation", "d4 Nf6 c4 e6 Nc3 Bb4 e3"),
    ("E21", "Nimzo-Indian Defense: Three Knights Variation", "d4 Nf6 c4 e6 Nc3 Bb4 Nf3"),
    ("E12", "Queen's Indian Defense", "d4 Nf6 c4 e6 Nf3 b6"),
    ("E11", "Bogo-Indian Defense", "d4 Nf6 c4 e6 Nf3 Bb4+"),
    ("E00", "Catalan Opening", "d4 Nf6 c4 e6 g3"),
    ("E04", "Catalan Opening: Open Defense", "d4 Nf6 c4 e6 g3 d5 Bg2 dxc4"),
    ("E06", "Catalan Opening: Closed", "d4 Nf6 c4 e6 g3 d5 Bg2 Be7"),
    ("A80", "Dutch Defense", "d4 f5"),
    ("A84", "Dutch Defense: Normal Variation", "d4 f5 c4"),
    ("A40", "Queen's Pawn Opening", "d4"),
    ("A40", "Englund Gambit", "d4 e5"),
    ("A40", "Horwitz Defense", "d4 e6"),
    ("A41", "Old Indian Defense", "d4 d6"),
    ("A43", "Old Benoni Defense", "d4 c5"),
    ("A40", "Queen's Pawn Opening: Modern Defense", "d4 g6"),
    # --- Flank openings -------------------------------------------------------------------
    ("A10", "English Opening", "c4"),
    ("A20", "English Opening: King's English Variation", "c4 e5"),
    ("A30", "English Opening: Symmetrical Variation", "c4 c5"),
    ("A15", "English Opening: Anglo-Indian Defense", "c4 Nf6"),
    ("A13", "English Opening: Agincourt Defense", "c4 e6"),
    ("A04", "Réti Opening", "Nf3"),
    ("A05", "Réti Opening: Anglo-Indian", "Nf3 Nf6"),
    ("A06", "Réti Opening: Queen's Pawn", "Nf3 d5"),
    ("A07", "King's Indian Attack", "Nf3 d5 g3"),
    ("A09", "Réti Opening: Réti Gambit", "Nf3 d5 c4"),
    ("A01", "Nimzo-Larsen Attack", "b3"),
    ("A02", "Bird's Opening", "f4"),
    ("A03", "Bird's Opening: Dutch Variation", "f4 d5"),
    ("A00", "Polish Opening", "b4"),
    ("A00", "Grob Opening", "g4"),
    ("A00", "Hungarian Opening", "g3"),
    ("A00", "Van't Kruijs Opening", "e3"),
    ("A00", "Van Geet Opening", "Nc3"),
]


@dataclass
class BookNode:
    name: str | None = None
    eco: str | None = None
    children: dict[str, "BookNode"] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.children is None:
            self.children = {}


def _build() -> BookNode:
    root = BookNode()
    for eco, name, line in LINES:
        node = root
        for san in line.split():
            node = node.children.setdefault(san, BookNode())
        node.name, node.eco = name, eco
    return root


ROOT = _build()
MAX_DEPTH = max(len(line.split()) for _, _, line in LINES)


def lookup(sans: list[str], player_color: str) -> dict[str, Any]:
    """Follow the game's moves through the book.

    Returns the deepest named opening reached, the ply at which the game left the book
    (1-based; None if the whole book prefix was matched), who left it, the move played
    there and the alternatives the book knew at that point."""
    node = ROOT
    name = eco = None
    book_plies = 0
    deviation_ply = None
    deviated_by = None
    played = None
    book_moves: list[str] = []
    for i, san in enumerate(sans):
        nxt = node.children.get(san)
        if nxt is None:
            if node.children:  # the book still had moves here: this is a real deviation
                deviation_ply = i + 1
                deviated_by = "player" if ((i % 2 == 0) == (player_color == "white")) else "opponent"
                played = san
                book_moves = sorted(node.children)
            break
        node = nxt
        book_plies = i + 1
        if node.name:
            name, eco = node.name, node.eco
    return {
        "name": name,
        "eco": eco,
        "book_plies": book_plies,
        "deviation_ply": deviation_ply,
        "deviated_by": deviated_by,
        "played": played,
        "book_moves": book_moves,
    }


def family_of(name: str | None) -> str | None:
    return name.split(":")[0].strip() if name else None
