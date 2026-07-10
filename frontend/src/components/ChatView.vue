<script setup lang="ts">
import { ref, nextTick, onMounted } from "vue";
import { useChatStore, type Source } from "../stores/chat";
import { queryRAG } from "../config/api";
import MarkdownView from "./MarkdownView.vue";
import SourceBubble from "./SourceBubble.vue";

const store = useChatStore();
const input = ref("");
const chatEl = ref<HTMLElement | null>(null);
const inputEl = ref<HTMLTextAreaElement | null>(null);

function scrollToBottom() {
  nextTick(() => { chatEl.value?.scrollTo({ top: chatEl.value.scrollHeight, behavior: "smooth" }); });
}

async function send() {
  const q = input.value.trim();
  if (!q || store.loading) return;
  input.value = "";
  store.loading = true;

  store.addMessage({ id: store.generateId(), role: "user", content: q });
  scrollToBottom();

  // 占位消息（流式效果）
  const msgId = store.generateId();
  store.addMessage({ id: msgId, role: "assistant", content: "", streaming: true });
  scrollToBottom();

  // 打字机效果：逐字显示
  let displayed = "";
  const speedMs = 30;

  try {
    const result = await queryRAG(q);
    const fullText = result.answer || "";

    for (let i = 0; i < fullText.length; i++) {
      displayed += fullText[i];
      store.updateLastMessage(displayed);
      if (i % 3 === 0) await sleep(speedMs); // 每 3 字停一次
      scrollToBottom();
    }

    store.finalizeLastMessage(
      result.sources || [],
      result.intent || "",
      result.latency_ms || null
    );
  } catch (e: any) {
    store.updateLastMessage(`> 请求失败: ${e.message}`);
    store.finalizeLastMessage([], "", null);
  }
  scrollToBottom();
}

function sleep(ms: number) { return new Promise((r) => setTimeout(r, ms)); }

function handleKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
}

onMounted(() => inputEl.value?.focus());
</script>

<template>
  <div class="flex flex-col h-full">
    <!-- 消息列表 -->
    <div ref="chatEl" class="flex-1 overflow-y-auto px-4 py-6 space-y-5">
      <div v-if="store.messages.length === 0" class="flex flex-col items-center justify-center h-full text-gray-600 gap-4">
        <div class="w-16 h-16 rounded-2xl bg-gradient-to-br from-cyan-400/20 to-blue-600/20 flex items-center justify-center text-3xl">⛏️</div>
        <p class="text-lg font-medium">问点什么</p>
        <p class="text-sm">LME 铜价 / 稀土政策 / 锂矿新闻 ...</p>
      </div>

      <template v-for="msg in store.messages" :key="msg.id">
        <!-- 用户气泡 -->
        <div v-if="msg.role === 'user'" class="flex justify-end">
          <div class="max-w-[70%] rounded-2xl rounded-br-md bg-gradient-to-br from-cyan-500/20 to-blue-600/20 border border-cyan-500/20 px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap">
            {{ msg.content }}
          </div>
        </div>

        <!-- 助手气泡 -->
        <div v-else class="flex justify-start">
          <div class="max-w-[85%] space-y-3">
            <!-- 意图标签 -->
            <div v-if="msg.intent" class="flex gap-2 text-xs">
              <span class="px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
                {{ { price: "📊 价格", policy: "📜 政策", news: "📰 新闻", general: "💬 综合" }[msg.intent] || msg.intent }}
              </span>
              <span v-if="msg.latencyMs" class="px-2 py-0.5 rounded-full bg-gray-800 text-gray-400">
                {{ msg.latencyMs.total }}ms
              </span>
            </div>

            <!-- 回答（流式打字机） -->
            <div v-if="msg.content" class="markdown-body">
              <MarkdownView :content="msg.content" :sources="msg.sources || []" />
            </div>
            <div v-else class="flex items-center gap-2 text-gray-500">
              <span class="inline-block w-5 h-5 border-2 border-cyan-400/40 border-t-cyan-400 rounded-full animate-spin" />
              思考中...
            </div>

            <!-- 光标（正在流式时） -->
            <span v-if="msg.streaming" class="inline-block w-2 h-4 bg-cyan-400 animate-blink" />

            <!-- 来源气泡 -->
            <div v-if="msg.sources && msg.sources.length > 0 && !msg.streaming" class="flex flex-wrap gap-2 pt-1">
              <SourceBubble v-for="(src, i) in msg.sources" :key="i" :source="src" :index="i" />
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- 输入区 -->
    <div class="shrink-0 border-t border-gray-800 px-4 py-4">
      <div class="max-w-4xl mx-auto flex gap-3">
        <n-input
          ref="inputEl"
          v-model:value="input"
          type="textarea"
          :autosize="{ minRows: 1, maxRows: 4 }"
          placeholder="输入你的问题..."
          :disabled="store.loading"
          @keydown="handleKeydown"
          class="flex-1"
        />
        <n-button
          :loading="store.loading"
          :disabled="!input.trim()"
          @click="send"
          type="primary"
          class="self-end"
          size="large"
        >
          发送
        </n-button>
      </div>
    </div>
  </div>
</template>
