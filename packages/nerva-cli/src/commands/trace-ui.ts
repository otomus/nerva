/**
 * Trace UI HTML page served by the dev server.
 *
 * Returns a self-contained HTML page that displays trace events as a tree,
 * auto-refreshing every 2 seconds from the `/traces` endpoint.
 */

/**
 * Builds the trace UI HTML page as a string.
 *
 * The page fetches `/traces` every 2 seconds and renders spans
 * as an indented, color-coded tree in a monospace font.
 *
 * @returns Complete HTML document string
 */
export function buildTraceHtml(): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Nerva Trace UI</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: "SF Mono", "Menlo", "Consolas", monospace;
      font-size: 13px;
      background: #1a1a2e;
      color: #e0e0e0;
      padding: 24px;
    }
    h1 { color: #7fdbca; margin-bottom: 16px; font-size: 18px; }
    .status { color: #888; margin-bottom: 16px; font-size: 11px; }
    .trace-list { list-style: none; }
    .trace-item { margin-bottom: 2px; white-space: pre; }
    .channel-agent { color: #c792ea; }
    .channel-tool { color: #82aaff; }
    .channel-middleware { color: #ffcb6b; }
    .channel-router { color: #f78c6c; }
    .channel-system { color: #89ddff; }
    .channel-unknown { color: #a0a0a0; }
    .timestamp { color: #666; }
    .empty { color: #555; font-style: italic; margin-top: 12px; }
  </style>
</head>
<body>
  <h1>Nerva Trace UI</h1>
  <div class="status" id="status">Connecting...</div>
  <ul class="trace-list" id="traces"></ul>

  <script>
    const CHANNEL_COLORS = {
      agent: 'channel-agent',
      tool: 'channel-tool',
      middleware: 'channel-middleware',
      router: 'channel-router',
      system: 'channel-system',
    };

    function channelClass(channel) {
      return CHANNEL_COLORS[channel] || 'channel-unknown';
    }

    function escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    function renderTrace(trace, depth) {
      const indent = '  '.repeat(depth);
      const ch = trace.channel || 'unknown';
      const ts = trace.timestamp ? new Date(trace.timestamp).toISOString().slice(11, 23) : '';
      const msg = escapeHtml(trace.message || trace.event || JSON.stringify(trace));

      let html = '<li class="trace-item">';
      html += '<span class="timestamp">' + escapeHtml(ts) + '</span> ';
      html += indent;
      html += '<span class="' + channelClass(ch) + '">[' + escapeHtml(ch) + ']</span> ';
      html += msg;
      html += '</li>';

      if (Array.isArray(trace.children)) {
        for (const child of trace.children) {
          html += renderTrace(child, depth + 1);
        }
      }

      return html;
    }

    async function refresh() {
      try {
        const res = await fetch('/traces');
        const data = await res.json();
        const list = document.getElementById('traces');
        const status = document.getElementById('status');

        if (!Array.isArray(data) || data.length === 0) {
          list.innerHTML = '<li class="empty">No traces yet.</li>';
        } else {
          list.innerHTML = data.map(function(t) { return renderTrace(t, 0); }).join('');
        }

        status.textContent = 'Last updated: ' + new Date().toLocaleTimeString() +
          ' (' + (data.length || 0) + ' traces)';
      } catch (err) {
        document.getElementById('status').textContent = 'Error: ' + err.message;
      }
    }

    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>`;
}
