/* Minimal chessboard renderer driven by FEN strings. Unicode glyphs, CSS squares, SVG arrows. */
(function () {
  const GLYPH = { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };
  const FILES = "abcdefgh";

  function parseFen(fen) {
    const board = {};
    const rows = fen.split(" ")[0].split("/");
    rows.forEach((row, ri) => {
      let file = 0;
      for (const ch of row) {
        if (/\d/.test(ch)) { file += parseInt(ch, 10); continue; }
        const sq = FILES[file] + (8 - ri);
        board[sq] = { color: ch === ch.toUpperCase() ? "w" : "b", type: ch.toLowerCase() };
        file++;
      }
    });
    return board;
  }

  class Board {
    constructor(container, opts) {
      this.el = container;
      this.opts = opts || {};
      this.flipped = !!this.opts.flipped;
      this.fen = this.opts.fen || "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
      this.marks = {};
      this.arrows = [];
      this.onSquare = null;
      this.render();
    }
    squares() {
      const out = [];
      const ranks = this.flipped ? [1, 2, 3, 4, 5, 6, 7, 8] : [8, 7, 6, 5, 4, 3, 2, 1];
      const files = this.flipped ? [7, 6, 5, 4, 3, 2, 1, 0] : [0, 1, 2, 3, 4, 5, 6, 7];
      for (const r of ranks) for (const f of files) out.push(FILES[f] + r);
      return out;
    }
    setPosition(fen, marks, arrows) {
      this.fen = fen;
      this.marks = marks || {};
      this.arrows = arrows || [];
      this.render();
    }
    flip() { this.flipped = !this.flipped; this.render(); }
    render() {
      const pieces = parseFen(this.fen);
      this.el.classList.add("board");
      this.el.classList.toggle("puzzle", !!this.opts.interactive);
      this.el.innerHTML = "";
      const sqs = this.squares();
      sqs.forEach((sq, i) => {
        const f = FILES.indexOf(sq[0]);
        const r = parseInt(sq[1], 10);
        const div = document.createElement("div");
        div.className = "sq " + (((f + r) % 2 === 0) ? "dark" : "light");
        div.dataset.sq = sq;
        if (this.marks[sq]) div.classList.add(this.marks[sq]);
        const p = pieces[sq];
        if (p) {
          const span = document.createElement("span");
          span.className = "piece " + p.color;
          span.textContent = GLYPH[p.type];
          div.appendChild(span);
        }
        if (i >= 56) { const c = document.createElement("span"); c.className = "coord file"; c.textContent = sq[0]; div.appendChild(c); }
        if (i % 8 === 0) { const c = document.createElement("span"); c.className = "coord rank"; c.textContent = sq[1]; div.appendChild(c); }
        if (this.opts.interactive) div.addEventListener("click", () => this.onSquare && this.onSquare(sq));
        this.el.appendChild(div);
      });
      this.renderArrows();
    }
    center(sq) {
      const f = FILES.indexOf(sq[0]);
      const r = parseInt(sq[1], 10);
      const col = this.flipped ? 7 - f : f;
      const row = this.flipped ? r - 1 : 8 - r;
      return [col * 12.5 + 6.25, row * 12.5 + 6.25];
    }
    renderArrows() {
      const NS = "http://www.w3.org/2000/svg";
      const svg = document.createElementNS(NS, "svg");
      svg.setAttribute("viewBox", "0 0 100 100");
      svg.setAttribute("class", "arrows");
      for (const a of this.arrows) {
        const [x1, y1] = this.center(a.from);
        const [x2, y2] = this.center(a.to);
        const dx = x2 - x1, dy = y2 - y1;
        const len = Math.hypot(dx, dy) || 1;
        const ux = dx / len, uy = dy / len;
        const head = 3.2;
        const ex = x2 - ux * head * 0.8, ey = y2 - uy * head * 0.8;
        const line = document.createElementNS(NS, "line");
        line.setAttribute("x1", x1 + ux * 3); line.setAttribute("y1", y1 + uy * 3);
        line.setAttribute("x2", ex); line.setAttribute("y2", ey);
        line.setAttribute("stroke", a.color); line.setAttribute("stroke-width", 1.6); line.setAttribute("stroke-linecap", "round");
        line.setAttribute("opacity", 0.85);
        svg.appendChild(line);
        const poly = document.createElementNS(NS, "polygon");
        const px = -uy, py = ux;
        poly.setAttribute("points", `${x2},${y2} ${x2 - ux * head + px * head * 0.6},${y2 - uy * head + py * head * 0.6} ${x2 - ux * head - px * head * 0.6},${y2 - uy * head - py * head * 0.6}`);
        poly.setAttribute("fill", a.color); poly.setAttribute("opacity", 0.85);
        svg.appendChild(poly);
      }
      this.el.appendChild(svg);
    }
  }

  window.ChessBoard = { Board, parseFen };
})();
