<script setup lang="ts">
import { darkTheme } from "naive-ui";
import ChatView from "./components/ChatView.vue";
</script>

<template>
  <n-config-provider :theme="darkTheme" :locale="zhCN">
    <n-message-provider>
      <div class="h-screen flex flex-col">
        <!-- 顶栏 -->
        <header class="shrink-0 border-b border-gray-800 bg-gray-950/80 backdrop-blur-lg px-6 py-3 flex items-center gap-3">
          <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-cyan-400 to-blue-600 flex items-center justify-center text-white font-bold text-sm">O</div>
          <div>
            <h1 class="text-lg font-semibold tracking-tight">OreMind</h1>
            <p class="text-xs text-gray-500">矿业情报 RAG</p>
          </div>
          <div class="ml-auto flex items-center gap-2 text-xs text-gray-500">
            <span class="inline-block w-2 h-2 rounded-full bg-green-500" />
            2530 向量 · BGE · DeepSeek
          </div>
        </header>
        <!-- 聊天 -->
        <ChatView class="flex-1 overflow-hidden" />
      </div>
    </n-message-provider>
  </n-config-provider>
</template>
