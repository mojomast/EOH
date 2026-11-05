// Careful subset of the OSM opening_hours grammar, for the pane's "open now" filter.
// Supported: "24/7"; rules joined by ";" (a later rule replaces earlier ones for the days it names,
// as in the spec); weekday selectors Mo..Su with ranges and lists ("Mo-Fr,Su", "Fr-Mo");
// time spans "HH:MM-HH:MM" (lists with ","; ends past midnight, e.g. 18:00-02:00 or 18:00-26:00);
// "off"/"closed"; a rule with times but no days means every day. Anything else (PH/SH, months,
// weeks, dates, sunrise/sunset, "+" open ends, comments, "||" fallbacks) makes the answer
// UNKNOWN (null) rather than a guess. isOpen(value, date) -> true | false | null.
(() => {
  const DAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']
  const dayIdx = (d) => DAYS.indexOf(d)
  const parseDays = (sel) => {
    const out = new Set()
    for (const part of sel.split(',')) {
      const m = part.trim().match(/^(Mo|Tu|We|Th|Fr|Sa|Su)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))?$/)
      if (!m) return null
      const a = dayIdx(m[1]), b = m[2] ? dayIdx(m[2]) : a
      for (let i = a; ; i = (i + 1) % 7) { out.add(i); if (i === b) break }
    }
    return out
  }
  const toMin = (t) => { const m = t.match(/^(\d{1,2}):(\d{2})$/); if (!m) return null; const h = +m[1], mi = +m[2]; return h > 48 || mi > 59 ? null : h * 60 + mi }
  const parseTimes = (s) => {
    const spans = []
    for (const part of s.split(',')) {
      const m = part.trim().match(/^(\d{1,2}:\d{2})-(\d{1,2}:\d{2})$/)
      if (!m) return null
      const a = toMin(m[1]); let b = toMin(m[2])
      if (a == null || b == null) return null
      if (b <= a) b += 1440  // overnight
      spans.push([a, b])
    }
    return spans
  }
  // -> array of 7 entries (Mo..Su): array of [startMin, endMin] spans (end may exceed 1440), or null.
  const parse = (value) => {
    const v = String(value ?? '').trim()
    if (!v || v.length > 400) return null
    if (v === '24/7') return DAYS.map(() => [[0, 1440]])
    if (/\|\||"|\+|PH|SH|sunrise|sunset|dawn|dusk|week|[A-Z][a-z]{2} ?\d|\d{4}/.test(v)) return null
    const week = DAYS.map(() => null)
    let any = false
    for (const raw of v.split(';')) {
      const rule = raw.trim()
      if (!rule) continue
      const m = rule.match(/^((?:Mo|Tu|We|Th|Fr|Sa|Su)(?:[-,](?:Mo|Tu|We|Th|Fr|Sa|Su))*)?\s*(.*)$/)
      if (!m) return null
      const days = m[1] ? parseDays(m[1]) : new Set([0, 1, 2, 3, 4, 5, 6])
      const rest = m[2].trim()
      if (!days) return null
      let spans
      if (/^(off|closed)$/i.test(rest)) spans = []
      else if (rest === '24/7' || rest === '00:00-24:00') spans = [[0, 1440]]
      else { spans = parseTimes(rest); if (!spans) return null }
      for (const d of days) week[d] = spans
      any = true
    }
    if (!any) return null
    return week.map((s) => s || [])  // days never named are closed (spec default)
  }
  const isOpen = (value, date = new Date()) => {
    const w = parse(value)
    if (!w) return null
    const d = (date.getDay() + 6) % 7, mins = date.getHours() * 60 + date.getMinutes()
    if (w[d].some(([a, b]) => mins >= a && mins < b)) return true
    const prev = (d + 6) % 7  // yesterday's span running past midnight
    if (w[prev].some(([a, b]) => b > 1440 && mins + 1440 >= a && mins + 1440 < b)) return true
    return false
  }
  const api = { parse, isOpen }
  if (typeof window !== 'undefined') window.__hermesOpeningHours = api
  if (typeof module !== 'undefined') module.exports = api
})()
