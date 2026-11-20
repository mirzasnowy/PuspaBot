/**
 * Interactive-widget helpers for the Chat screen (P-14): the
 * safe formula evaluator for the calculator widget, mirrored
 * from the server module `puspa/pwa/chat_widgets.py` so the
 * client computes the live result as the user types and the
 * server is the authoritative check (`POST
 * /api/chat/widgets/compute`).
 *
 * The formula is data, never code: a small recursive-descent
 * parser accepts only numbers, input names, `+ - * / % **`,
 * parentheses, and the calls `min`/`max`/`abs`/`round`. Anything
 * else throws, exactly like the server's AST whitelist. The
 * two evaluators are pinned to each other by the shared cases
 * in `chatWidgets.test.ts` and `tests/test_chat_widgets.py`.
 */

export type WidgetValueFormat = "number" | "rupiah" | "percent";

/** Render a computed number for display, same shapes as the server. */
export function formatWidgetValue(value: number, format: WidgetValueFormat): string {
  if (format === "rupiah") {
    return `Rp${Math.trunc(value)
      .toString()
      .replace(/\B(?=(\d{3})+(?!\d))/g, ".")}`;
  }
  if (format === "percent") {
    return `${value.toFixed(1)}%`;
  }
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

type Token =
  | { type: "number"; value: number }
  | { type: "name"; value: string }
  | { type: "op"; value: string }
  | { type: "lparen" }
  | { type: "rparen" }
  | { type: "comma" };

function tokenize(formula: string): Token[] {
  const tokens: Token[] = [];
  let i = 0;
  while (i < formula.length) {
    const ch = formula[i];
    if (ch === " " || ch === "\t" || ch === "\n") {
      i++;
      continue;
    }
    if (/[0-9.]/.test(ch)) {
      let j = i;
      while (j < formula.length && /[0-9.]/.test(formula[j])) j++;
      const text = formula.slice(i, j);
      const value = Number(text);
      if (!Number.isFinite(value) || text.split(".").length > 2) {
        throw new Error(`formula has a bad number ${JSON.stringify(text)}`);
      }
      tokens.push({ type: "number", value });
      i = j;
      continue;
    }
    if (/[A-Za-z_]/.test(ch)) {
      let j = i;
      while (j < formula.length && /[A-Za-z0-9_]/.test(formula[j])) j++;
      tokens.push({ type: "name", value: formula.slice(i, j) });
      i = j;
      continue;
    }
    if (ch === "*" && formula[i + 1] === "*") {
      tokens.push({ type: "op", value: "**" });
      i += 2;
      continue;
    }
    if ("+-*/%".includes(ch)) {
      tokens.push({ type: "op", value: ch });
      i++;
      continue;
    }
    if (ch === "(") {
      tokens.push({ type: "lparen" });
      i++;
      continue;
    }
    if (ch === ")") {
      tokens.push({ type: "rparen" });
      i++;
      continue;
    }
    if (ch === ",") {
      tokens.push({ type: "comma" });
      i++;
      continue;
    }
    throw new Error(`formula has an unexpected character ${JSON.stringify(ch)}`);
  }
  return tokens;
}

/**
 * Evaluate a calculator formula against the input values.
 * Throws on an unknown input, division by zero, a disallowed
 * construct, or a non-finite result.
 */
export function evaluateFormula(formula: string, values: Record<string, number>): number {
  if (typeof formula !== "string" || formula.trim().length === 0) {
    throw new Error("formula must be a non-empty string");
  }
  if (formula.length > 500) {
    throw new Error("formula exceeds the 500-character cap");
  }
  const tokens = tokenize(formula);
  let pos = 0;

  function peek(): Token | undefined {
    return tokens[pos];
  }

  function consume(): Token | undefined {
    return tokens[pos++];
  }

  // Lowest precedence: addition and subtraction.
  function parseAddSub(): number {
    let left = parseMulDivMod();
    for (;;) {
      const token = peek();
      if (token?.type === "op" && (token.value === "+" || token.value === "-")) {
        pos++;
        const right = parseMulDivMod();
        left = token.value === "+" ? left + right : left - right;
      } else {
        return left;
      }
    }
  }

  function parseMulDivMod(): number {
    let left = parseUnary();
    for (;;) {
      const token = peek();
      if (token?.type === "op" && ["*", "/", "%"].includes(token.value)) {
        pos++;
        const right = parseUnary();
        if (token.value === "*") left *= right;
        if (token.value === "/") {
          if (right === 0) throw new Error("formula divides by zero");
          left /= right;
        }
        if (token.value === "%") {
          if (right === 0) throw new Error("formula divides by zero");
          left %= right;
        }
      } else {
        return left;
      }
    }
  }

  // Power binds tighter than unary minus on the left (`-2 ** 2`
  // is `-(2 ** 2)` on the server, matching Python), and is
  // right-associative on the right (`2 ** 3 ** 2` is `2 ** 9`).
  // The exponent is a unary expression, so `2 ** -1` works.
  function parsePower(): number {
    const base = parsePrimary();
    const token = peek();
    if (token?.type === "op" && token.value === "**") {
      pos++;
      const exponent = parseUnary();
      const result = base ** exponent;
      if (!Number.isFinite(result)) throw new Error("formula result must be finite");
      return result;
    }
    return base;
  }

  function parseUnary(): number {
    const token = peek();
    if (token?.type === "op" && (token.value === "-" || token.value === "+")) {
      pos++;
      const operand = parseUnary();
      return token.value === "-" ? -operand : operand;
    }
    return parsePower();
  }

  function parsePrimary(): number {
    const token = consume();
    if (token === undefined) throw new Error("formula ends unexpectedly");
    if (token.type === "number") return token.value;
    if (token.type === "lparen") {
      const value = parseAddSub();
      const closing = consume();
      if (closing?.type !== "rparen") throw new Error("formula has an unmatched parenthesis");
      return value;
    }
    if (token.type === "name") {
      const next = peek();
      if (next?.type === "lparen") {
        return parseCall(token.value);
      }
      if (!(token.value in values)) {
        throw new Error(`formula uses unknown input ${JSON.stringify(token.value)}`);
      }
      return values[token.value];
    }
    throw new Error("formula has an unexpected token");
  }

  function parseCall(name: string): number {
    if (!["min", "max", "abs", "round"].includes(name)) {
      throw new Error("formula calls must be min, max, abs, or round");
    }
    consume(); // the "("
    const args: number[] = [];
    if (peek()?.type !== "rparen") {
      for (;;) {
        args.push(parseAddSub());
        const token = peek();
        if (token?.type === "comma") {
          pos++;
          continue;
        }
        break;
      }
    }
    const closing = consume();
    if (closing?.type !== "rparen") throw new Error("formula has an unmatched parenthesis");
    if (args.length < 1 || args.length > 2) {
      throw new Error("formula calls take 1 or 2 arguments");
    }
    if (name === "min") return Math.min(...args);
    if (name === "max") return Math.max(...args);
    if (name === "abs") {
      if (args.length !== 1) throw new Error("abs takes exactly 1 argument");
      return Math.abs(args[0]);
    }
    const places = args.length === 2 ? Math.trunc(args[1]) : 0;
    const factor = 10 ** places;
    return Math.round(args[0] * factor) / factor;
  }

  const result = parseAddSub();
  if (pos !== tokens.length) {
    throw new Error("formula has trailing characters");
  }
  if (!Number.isFinite(result)) {
    throw new Error("formula result must be finite");
  }
  return result;
}
