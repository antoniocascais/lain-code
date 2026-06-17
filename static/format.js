// Shared display formatters. Loaded as a plain script (browser global) and
// require()-able from node tests via the module guard below.
function shortModelName(m) {
  // Keep full family+version so opus-4-8 doesn't collapse to "opus-4".
  const parts = m.replace(/^claude-/, '').replace(/\[[^\]]*\]$/, '').split('-');
  if (/^\d{6,}$/.test(parts[parts.length - 1])) parts.pop();
  return parts.join('-');
}

if (typeof module !== 'undefined' && module.exports) module.exports = { shortModelName };
