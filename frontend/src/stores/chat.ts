import { defineStore } from "pinia";
import { ref } from "vue";

export interface Source {
  title: string;
  url: string;
  source: string;
  score: number;
  scores?: { rrf?: number; rerank?: number; confidence?: number };
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  intent?: string;
  latencyMs?: { retrieval: number; llm: number; total: number };
  streaming?: boolean;
}

export const useChatStore = defineStore("chat", () => {
  const messages = ref<Message[]>([]);
  const loading = ref(false);

  function addMessage(msg: Message) {
    // 替换最后一条如果是 streaming
    const last = messages.value[messages.value.length - 1];
    if (msg.role === "assistant" && last?.role === "assistant" && last.streaming) {
      messages.value[messages.value.length - 1] = msg;
    } else {
      messages.value.push(msg);
    }
  }

  function updateLastMessage(content: string) {
    const last = messages.value[messages.value.length - 1];
    if (last?.role === "assistant") {
      last.content = content;
    }
  }

  function finalizeLastMessage(sources: Source[], intent: string, latencyMs: any) {
    const last = messages.value[messages.value.length - 1];
    if (last?.role === "assistant") {
      last.sources = sources;
      last.intent = intent;
      last.latencyMs = latencyMs;
      last.streaming = false;
    }
    loading.value = false;
  }

  function clearMessages() {
    messages.value = [];
  }

  function generateId() {
    return `msg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  }

  return { messages, loading, addMessage, updateLastMessage, finalizeLastMessage, clearMessages, generateId };
});
