import type { HowToResult } from "../../types";

export interface EditableTextBlock {
  kind: "heading" | "paragraph";
  text: string;
}

export interface ParsedEditableText {
  title: string;
  hasTitleMarker: boolean;
  blocks: EditableTextBlock[];
}

export function editableDocumentText(content: HowToResult): string {
  if (content.user_text != null) return content.user_text;
  const lines = [`# ${content.title}`];
  const add = (heading: string, texts: string[]) => {
    if (texts.length) lines.push(`## ${heading}`, ...texts);
  };
  if (content.summary && "sections" in content.summary) {
    for (const section of content.summary.sections) add(section.heading, [section.text]);
  } else if (content.summary) {
    const names = { core_summary: "核心摘要", discussions: "主要讨论", decisions: "结论", action_items: "待办", open_questions: "待解决问题" };
    for (const field of Object.keys(names) as (keyof typeof names)[]) add(names[field], content.summary[field].map(item => item.text));
  } else {
    add("准备", content.preparations.map(item => item.text));
    const steps = content.steps.flatMap((step, index) => [
      `${index + 1}. ${step.instruction}`,
      ...[
        step.location && `位置：${step.location}`,
        step.parameters.length && `参数：${step.parameters.join("、")}`,
        step.conditions.length && `条件：${step.conditions.join("、")}`,
        step.warnings.length && `注意：${step.warnings.join("、")}`,
        step.expected_result && `预期结果：${step.expected_result}`,
      ].filter(Boolean) as string[],
    ]);
    add("步骤", steps);
    add("检查", content.checks.map(item => item.text));
    add("仍缺信息", content.missing_information);
    add("不同说法", content.conflicts.map(item => [item.description, item.claim_a, item.claim_b, item.resolution || "尚未解决"].join("\n")));
  }
  for (const item of content.professional_extensions || []) {
    add(`专业补充 · ${item.heading}（AI 延伸，不代表原聊天）`, [item.text]);
  }
  return lines.join("\n\n");
}

export function editableTitle(value: string): string | null {
  const first = value.split(/\r?\n/).find(line => line.trim());
  if (!first?.startsWith("# ") || first.startsWith("## ")) return null;
  return first.slice(2).trim() || null;
}

export function restoreDocumentTitleMarker(value: string, savedTitle: string): string {
  const lines = value.split(/\r?\n/);
  const firstIndex = lines.findIndex(line => line.trim());
  if (firstIndex >= 0 && lines[firstIndex].trim() === savedTitle.trim()) {
    lines[firstIndex] = `# ${savedTitle.trim()}`;
    return lines.join("\n");
  }
  return `# ${savedTitle.trim()}\n\n${value}`;
}

export function parseEditableText(value: string, fallbackTitle: string): ParsedEditableText {
  const lines = value.split(/\r?\n/);
  const firstIndex = lines.findIndex(line => line.trim());
  const title = editableTitle(value);
  const hasTitleMarker = title !== null;
  const blocks: EditableTextBlock[] = [];
  const paragraph: string[] = [];
  const flush = () => {
    const text = paragraph.join("\n").trim();
    if (text) blocks.push({ kind: "paragraph", text });
    paragraph.length = 0;
  };
  const start = hasTitleMarker ? firstIndex + 1 : 0;
  for (const line of lines.slice(start)) {
    if (hasTitleMarker && line.startsWith("## ") && line.slice(3).trim()) {
      flush();
      blocks.push({ kind: "heading", text: line.slice(3).trim() });
    } else if (!line.trim()) {
      flush();
    } else {
      paragraph.push(line);
    }
  }
  flush();
  if (!hasTitleMarker && blocks[0]?.kind === "paragraph" && blocks[0].text === fallbackTitle) blocks.shift();
  return { title: title || fallbackTitle, hasTitleMarker, blocks };
}
