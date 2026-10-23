const taskLabels = new Set(["要求", "整理要求", "输出要求", "输出格式", "任务", "目标", "范围", "格式", "风格", "步骤", "注意", "主题", "关键词", "时间", "内容", "资料", "示例", "总结", "备注", "背景", "角色", "约束", "限制", "输入", "输出", "参考链接", "http", "https"]);
const speaker = (name: string) => !!name.trim() && !taskLabels.has(name.trim());
const blockHeader = /^([^：:\n]{1,100})[：:]\s*$/;
const inlineHeader = /^([^：:\n]{1,32})[：:][ \t]*(\S.*)$/;

/** Local routing only. The existing importer still validates every record. */
export function composerChatText(value: string): string | null {
  const text = value.replace(/\r\n?/g, "\n");
  const lines = text.split("\n");
  const first = lines.find(line => line.trim())?.trim() ?? "";
  if (!first) return null;
  // These are the existing importer's timestamped and copied-export dialects.
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}(?::\d{2})?\s+\S/.test(first)
    || /^\[\d{1,2}:\d{2}(?::\d{2})?\]\s*.+?[：:]\s*\S/.test(first)
    || /^.+:\s*\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$/.test(first)
    || /^[^：:\n]{1,100}:\u00a0/.test(first)) return text;
  const headers = lines.map(line => blockHeader.exec(line.trim())).filter(match => match && speaker(match[1]));
  const initial = blockHeader.exec(first);
  if (initial && speaker(initial[1]) && headers.length >= 2) return text;
  // Plain copied "name: message" turns use the same canonical block parser.
  // Require different speakers and reject task-field labels to avoid eating instructions.
  const inline = lines.map(line => inlineHeader.exec(line.trim()));
  const firstInline = inlineHeader.exec(first);
  const names = new Set(inline.filter(match => match && speaker(match[1])).map(match => match![1].trim()));
  if (!firstInline || !speaker(firstInline[1]) || names.size < 2
    || inline.some(match => match && !speaker(match[1]))) return null;
  return lines.map((line, index) => inline[index] ? `${inline[index]![1].trim()}：\n${inline[index]![2]}` : line).join("\n");
}
