export function cnDate(value?: string | null) {
  if (!value) return "暂无";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

export function formatNumber(value: unknown, digits = 2) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "—";
  return num.toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export const strategyLabels: Record<string, string> = {
  kdj: "KDJ",
  ma: "MA",
  boll: "BOLL",
  trump_social: "特朗普社媒",
  whitehouse: "白宫发言",
  whale: "巨鲸",
  translation: "大模型翻译",
  cleanup: "服务器清理"
};
