<script setup lang="ts">
import { ref, computed } from "vue";
import type { Source } from "../stores/chat";

const props = defineProps<{ source: Source; index: number }>();

const showTooltip = ref(false);

const sourceColors: Record<string, string> = {
  price: "bg-amber-500/10 text-amber-400 border-amber-500/30",
  policy: "bg-violet-500/10 text-violet-400 border-violet-500/30",
  news: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
};
const colorClass = sourceColors[props.source.source] || "bg-gray-500/10 text-gray-400 border-gray-500/30";

const label = computed(() =>
  props.source.source === "price" ? "价格"
  : props.source.source === "policy" ? "政策"
  : "新闻"
);

const scorePercent = computed(() => {
  const s = props.source.scores?.confidence ?? props.source.score ?? 0;
  return Math.min(100, Math.max(1, Math.round(s * 100)));
});
const scoreColor = computed(() =>
  scorePercent.value > 90 ? "bg-green-500"
  : scorePercent.value > 70 ? "bg-yellow-500"
  : "bg-gray-500"
);
const scoreTextColor = computed(() =>
  scorePercent.value > 90 ? "text-green-400"
  : scorePercent.value > 70 ? "text-yellow-400"
  : "text-gray-400"
);
</script>

<template>
  <div class="relative inline-flex" @mouseenter="showTooltip = true" @mouseleave="showTooltip = false">
    <!-- 引用标签 -->
    <span
      class="cite-tag inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium border"
      :class="colorClass"
    >
      <span>📎</span>
      {{ label }}<sup>{{ index + 1 }}</sup>
    </span>

    <!-- 毛玻璃气泡（相对定位） -->
    <div
      v-if="showTooltip"
      class="absolute z-50 top-full left-0 mt-2 w-72 glass-panel rounded-xl shadow-2xl p-4 text-sm pointer-events-auto"
      @mouseenter="showTooltip = true"
      @mouseleave="showTooltip = false"
    >
      <!-- 箭头 -->
      <div class="absolute -top-1.5 left-4 w-3 h-3 rotate-45 bg-gray-800/90" />

      <p class="font-medium text-gray-100 mb-1 truncate">{{ source.title || "来源" }}</p>
      <p class="text-xs text-gray-400 mb-2">置信度</p>

      <!-- 进度条 -->
      <div class="flex items-center gap-2">
        <div class="flex-1 h-1.5 rounded-full bg-gray-700 overflow-hidden">
          <div
            class="h-full rounded-full transition-all duration-500"
            :class="scoreColor"
            :style="{ width: scorePercent + '%' }"
          />
        </div>
        <span class="text-xs font-mono" :class="scoreTextColor">{{ scorePercent }}%</span>
      </div>

      <p class="text-xs text-gray-500 mt-2">{{ source.source }} · confidence {{ scorePercent }}%</p>

      <!-- 来源链接 -->
      <a
        v-if="source.url"
        :href="source.url"
        target="_blank"
        rel="noopener"
        class="mt-2 inline-flex items-center gap-1 text-xs text-cyan-400 hover:text-cyan-300 transition-colors"
      >
        🔗 查看原文
      </a>
    </div>
  </div>
</template>
