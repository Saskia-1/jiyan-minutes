import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ArtifactCard } from "./ArtifactCard";
import type { LocalApiClient } from "../../api/client";
const message = { message_id: "m1", role: "assistant" as const, text: "完成", created_at: "2026-10-23T00:00:00Z", run_id: "r1", document_id: "d1", version_id: "v1", artifact_revision: 1 };
const pair = { document_id: "d1", version_id: "v1", body: { filename: "正文.txt", text: "正文" }, reference: { filename: "参考.txt", text: "旧摘录" } };
afterEach(() => vi.restoreAllMocks());
it("fetches the exact version again on every download, including deletion before retention events", async () => {
 const textPair = vi.fn().mockResolvedValueOnce(pair).mockResolvedValueOnce(pair).mockResolvedValueOnce({ ...pair, reference: { filename: "参考.txt", text: "source_deleted" } });
 const urls = vi.fn((_blob: Blob) => "blob:test"); Object.assign(URL, { createObjectURL: urls, revokeObjectURL: vi.fn() });
 const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
 render(<ArtifactCard api={{ documentTextPair: textPair } as unknown as LocalApiClient} message={message} />);
 await screen.findByText("正文", { exact: true });
 expect(urls).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole("link", { name: "下载整理内容.txt" })); await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
 fireEvent.click(screen.getByRole("link", { name: "下载核查参考.txt" })); await waitFor(() => expect(click).toHaveBeenCalledTimes(2));
 expect(textPair.mock.calls).toEqual([["d1", "v1"], ["d1", "v1"], ["d1", "v1"]]);
 const blob = urls.mock.calls[1][0] as Blob;
 const reader = new FileReader(); const text = await new Promise<string>(resolve => { reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob); });
 expect(text).toContain("source_deleted"); expect(text).not.toContain("旧摘录");
});
it("does not create a blob when a download returns after the artifact was unmounted", async () => {
 let finish!: (value: typeof pair) => void;
 const textPair = vi.fn().mockResolvedValueOnce(pair).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
 const urls = vi.fn((_blob: Blob) => "blob:test"); Object.assign(URL, { createObjectURL: urls, revokeObjectURL: vi.fn() });
 const view = render(<ArtifactCard api={{ documentTextPair: textPair } as unknown as LocalApiClient} message={message} />);
 await screen.findByText("正文", { exact: true }); fireEvent.click(screen.getByRole("link", { name: "下载核查参考.txt" }));
 view.unmount(); finish(pair); await Promise.resolve(); expect(urls).not.toHaveBeenCalled();
});
