const API_BASE = "/api";

export async function queryRAG(
  question: string,
  topK = 3,
  signal?: AbortSignal
) {
  const res = await fetch(`${API_BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, top_k: topK }),
    signal,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/** 流式 RAG — 返回 ReadableStream<string> */
export function queryRAGStream(
  question: string,
  topK = 3
): { stream: ReadableStream<string>; abort: () => void } {
  const controller = new AbortController();

  const stream = new ReadableStream<string>({
    async start(ctrl) {
      try {
        const res = await fetch(`${API_BASE}/query/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, top_k: topK }),
          signal: controller.signal,
        });
        if (!res.ok) {
          ctrl.enqueue(`\n\n> 请求失败: ${await res.text()}`);
          ctrl.close();
          return;
        }
        const reader = res.body?.getReader();
        if (!reader) { ctrl.close(); return; }

        const decoder = new TextDecoder();
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const chunk = decoder.decode(value, { stream: true });
          // SSE 格式: "data: {...}\n\n"
          for (const line of chunk.split("\n")) {
            if (line.startsWith("data: ")) {
              const payload = JSON.parse(line.slice(6));
              if (payload.token) ctrl.enqueue(payload.token);
              if (payload.done) { ctrl.close(); return; }
            }
          }
        }
        ctrl.close();
      } catch (e: any) {
        if (e.name !== "AbortError") {
          ctrl.enqueue(`\n\n> 连接中断: ${e.message}`);
          ctrl.close();
        }
      }
    },
  });

  return { stream, abort: () => controller.abort() };
}
