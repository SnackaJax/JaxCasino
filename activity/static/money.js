// Credit amounts of any size. JavaScript numbers are only exact up to 2^53 (about 9 quadrillion), so the server sends
// bigger whole numbers as strings of digits. These helpers show both forms exactly and do the few sums the pages do
// with a balance (BigInt when it's big). Comparisons such as `cost > balance` keep working as they are: JavaScript
// turns the string into a number for those, which is close enough to compare a bet with a huge balance.

const SAFE = Number.MAX_SAFE_INTEGER;
const isBig = (v) => typeof v === 'bigint' || typeof v === 'string' || (typeof v === 'number' && Math.abs(v) > SAFE);
const big = (v) => (typeof v === 'bigint' ? v : BigInt(typeof v === 'number' ? Math.round(v) : String(v).trim()));
// a BigInt back to what the server would send: a number while it's exact, digits otherwise
const norm = (b) => (b >= -BigInt(SAFE) && b <= BigInt(SAFE) ? Number(b) : b.toString());

/** 1,234,567 in full (exact for any size). */
export function fmt(v) {
  if (typeof v === 'number' && (Math.abs(v) <= SAFE || !Number.isFinite(v))) return Math.round(v).toLocaleString('en-US');
  try { return big(v).toLocaleString('en-US'); } catch { return String(v); }
}

/** a credit amount as a plain number, for comparing and drawing (inexact past 2^53) */
export const num = (v) => (typeof v === 'number' ? v : Number(v));

/** a - b, exact at any size */
export function minus(a, b) {
  if (!isBig(a) && !isBig(b)) return a - b;
  try { return norm(big(a) - big(b)); } catch { return num(a) - num(b); }
}

/** a + b, exact at any size */
export function plus(a, b) {
  if (!isBig(a) && !isBig(b)) return a + b;
  try { return norm(big(a) + big(b)); } catch { return num(a) + num(b); }
}

// Short forms past a trillion: quadrillion (Qa), quintillion (Qi), sextillion (Sx), septillion (Sp), octillion (Oc),
// nonillion (No), decillion (Dc). Each page puts these in front of its own k/M/B/T list.
export const BIG_UNITS = [[1e33, 'Dc'], [1e30, 'No'], [1e27, 'Oc'], [1e24, 'Sp'], [1e21, 'Sx'], [1e18, 'Qi'], [1e15, 'Qa']];
/** 2.5k, 9.08M, 21.1Qi, 1.23e40: any amount in at most 3 digits and a unit (for the balance once it's 31 digits
 * or more, where the full number no longer fits the header; the tooltip keeps it) */
export function shortBig(v) {
  if (huge(v)) return huge(v);   // the digits, not the number: past 1e308 that's Infinity
  const n = num(v);
  for (const [d, u] of [...BIG_UNITS, [1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'k']]) {
    if (n >= d) { const x = n / d; return `${+x.toFixed(x < 10 ? 2 : x < 100 ? 1 : 0)}${u}`; }
  }
  return fmt(v);
}
/** past 1e36 even the biggest unit runs out: 1.23e40 */
export function huge(n) {
  if (!(num(n) >= 1e36)) return null;
  if (Number.isFinite(num(n))) {
    const [m, e] = num(n).toExponential(2).split('e');
    return `${+m}e${+e}`;
  }
  // past 1e308 a JavaScript number is Infinity: read it off the digits instead
  let d;
  try { d = big(n).toString(); } catch { return '∞'; }
  return `${+`${d[0]}.${d.slice(1, 3)}`}e${d.length - 1}`;
}
