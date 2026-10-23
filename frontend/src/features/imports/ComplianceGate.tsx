import { useEffect, useRef, useState, type ReactNode } from "react";

const VERSION = "minutes-use-2026-10-14-v1";
const STORAGE_KEY = "minutes-compliance";
function acknowledged() {
  try { return localStorage.getItem(STORAGE_KEY) === VERSION; } catch { return false; }
}

export function ComplianceGate({ children }: { children: ReactNode }) {
  const [accepted, setAccepted] = useState(acknowledged);
  const [open, setOpen] = useState(!accepted);
  const [checked, setChecked] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (open && dialog.current) {
      if (typeof dialog.current.showModal === "function") dialog.current.showModal();
      else dialog.current.setAttribute("open", "");
    } else { dialog.current?.close?.(); trigger.current?.focus(); }
  }, [open]);
  function accept() {
    if (!checked) return;
    try { localStorage.setItem(STORAGE_KEY, VERSION); } catch { /* This window remains acknowledged. */ }
    setAccepted(true); setOpen(false);
    trigger.current?.focus();
  }
  return <>
    <div inert={open}>
      <p className="muted">请仅导入你有权整理的资料。原文保留 7 天，文档与版本长期保留。
        <button type="button" ref={trigger} onClick={() => { setChecked(false); setOpen(true); }}>查看使用与合规提示</button>
      </p>
      {accepted && children}
    </div>
    {open && <dialog ref={dialog} aria-labelledby="compliance-title" onCancel={event => event.preventDefault()}>
      <h2 id="compliance-title">使用与合规提示</h2>
      <p>请确认你有权整理该对话，内容可能含他人个人信息。</p>
      <p>主对话中，上传或粘贴即同意本机导入，发送整理要求即同意本轮使用所选相关资料调用外部 AI。可随时停止；脱敏不保证覆盖全部个人信息。高级手动工具仍逐次授权。</p>
      <p>平台不识别保密等级，请勿导入涉密、受公司保密或合规限制、或无权外发的材料。</p>
      <p>文档与独立核查参考会长期保存在本机。原文自成功导入起保留 7 天，你可提前删除；到期停用并触发清理。文档内已有的摘录会保留。</p>
      <label className="check"><input type="checkbox" checked={checked} onChange={event => setChecked(event.target.checked)} />我已知悉</label>
      <button type="button" disabled={!checked} onClick={accept}>继续使用</button>
    </dialog>}
  </>;
}
