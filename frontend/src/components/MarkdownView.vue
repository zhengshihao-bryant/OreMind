<script setup lang="ts">
import { computed } from "vue";
import MarkdownIt from "markdown-it";
import type { Source } from "../stores/chat";

const props = defineProps<{ content: string; sources: Source[] }>();

const md = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
});

// 自定义引用渲染：将 [source:N] 替换为引用标签
function renderCitations(text: string): string {
  return text.replace(/\[source:(\d+)\]/g, (_, idx) => {
    const i = parseInt(idx);
    const src = props.sources[i];
    if (!src) return `[${idx}]`;
    return `<sup class="cite-tag inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs font-medium bg-cyan-500/10 text-cyan-400 border border-cyan-500/30" title="${src.title}">📎${i + 1}</sup>`;
  });
}

const html = computed(() => {
  const withCitations = renderCitations(props.content);
  return md.render(withCitations);
});
</script>

<template>
  <div class="markdown-body leading-7" v-html="html" />
</template>
