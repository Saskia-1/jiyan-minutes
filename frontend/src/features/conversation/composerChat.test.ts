import { describe, expect, it } from "vitest";
import { composerChatText } from "./composerChat";

describe("composerChatText", () => {
  it.each([
    "甲：\n先验证需求\n乙：\n再测试想法",
    "2026-10-23 10:00:00 甲\n先验证需求\n2026-10-23 10:01:00 乙\n再测试想法",
    "[10:00] 甲：先验证需求\n[10:01] 乙：再测试想法",
    "甲: 10-23 10:00:00\n先验证需求\n乙: 10-23 10:01:00\n再测试想法",
    "甲:\u00a0先验证需求\n乙:\u00a0再测试想法",
  ])("recognizes supported copied-chat dialects without changing their body: %s", text => {
    expect(composerChatText(text)).toBe(text);
  });

  it("converts plain inline speaker delimiters to the existing block format without losing continuations", () => {
    expect(composerChatText("甲：先验证需求\n补充一条想法\n乙: 再测试想法")).toBe("甲：\n先验证需求\n补充一条想法\n乙：\n再测试想法");
  });

  it.each(["整理这些资料里的观点", "整理要求：归纳观点\n输出格式：简洁列表", "目标：\n整理观点\n要求：\n列出依据", "背景：讨论很多，帮我整理\n约束：只输出三点", "角色：\n资料整理助手\n输入：\n本会话资料\n输出：\n三条观点", "参考链接：https://example.com\n保持原文观点", "今天想整理什么？\n列出两点", "标题：这是普通说明"]) (
    "leaves ordinary instructions as messages: %s", text => expect(composerChatText(text)).toBeNull(),
  );
});
