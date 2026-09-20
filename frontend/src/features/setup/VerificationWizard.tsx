import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, createClient, type LocalApiClient, type PreparedConsent, type SecretConsent } from "../../api/client";
import {
  isTrustedRealReport, sectionLabels, secretSectionLabels, sourceBaseUrls, type Conversation, type ConversationType, type EnvironmentReport, type Report, type Selection, type SetupOptions,
  type Source, type SourceConfiguration, type SourceId, type VerificationState,
} from "../../types";
import { SourceSetup } from "./SourceSetup";
import { ConsentCard } from "./ConsentCard";
import { ChatSearchPanel } from "../search/ChatSearchPanel";
import { ChatImportPanel } from "../imports/ChatImportPanel";

const TERMINAL_STATES = new Set<VerificationState>(["complete", "partial", "failed", "cancelled"]);
const POLL_DELAYS_MS = [350, 700, 1_400, 2_800, 4_000, 4_000, 4_000, 4_000] as const;
const STATE_LABELS: Record<VerificationState, string> = {
  pending_authorization: "等待授权", connecting: "正在连接", reading: "正在读取", saving: "正在保存",
  verifying: "正在核对", indexing: "正在索引", complete: "完成", partial: "部分完成",
  failed: "失败", cancelled: "已取消",
};
const TYPE_LABELS: Record<ConversationType, string> = { group: "群聊", friend: "单聊", user: "单聊", unknown: "类型未知" };

// P0 offers explicit fixed-offset zones; do not reinterpret input using the browser's timezone.
function instant(local: string, zone: string) {
  if (!local || !["Asia/Shanghai", "UTC"].includes(zone)) return "";
  const parsed = new Date(`${local}${zone === "UTC" ? "Z" : "+08:00"}`);
  return Number.isFinite(parsed.getTime()) ? parsed.toISOString() : "";
}
function dateParts(now: Date, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(now);
  const value = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return { year: Number(value.year), month: Number(value.month), day: Number(value.day) };
}
function defaultNaturalRange(now: Date) {
  const current = dateParts(now, "Asia/Shanghai");
  const today = Date.UTC(current.year, current.month - 1, current.day);
  const local = (milliseconds: number) => new Date(milliseconds).toISOString().slice(0, 10) + "T00:00:00";
  return { start: local(today - 6 * 86_400_000), end: local(today + 86_400_000) };
}
function shortSelectionId(value: string) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index++) hash = Math.imul(hash ^ value.charCodeAt(index), 0x01000193);
  return (hash >>> 0).toString(16).padStart(8, "0");
}
function conversationLabel(conversation: Conversation) {
  return `${conversation.display_name} · ${TYPE_LABELS[conversation.conversation_type]} · 标识 ${shortSelectionId(conversation.conversation_selection_id)}`;
}
function detectedVersion(environment: EnvironmentReport | null, key: keyof EnvironmentReport["applications"]) {
  const metadata = environment?.applications[key];
  return metadata?.installed && metadata.version !== "unknown" ? metadata.version : "";
}
function configurationFor(id: SourceId, source?: Source, environment: EnvironmentReport | null = null): SourceConfiguration {
  return {
    source_id: id, base_url: sourceBaseUrls[id], account_id: source?.account_id ?? "",
    client_version: source?.client_version ?? detectedVersion(environment, id),
    tool_version: source?.tool_version ?? detectedVersion(environment, id === "qq" ? "qce" : "tracememo"),
    account_version: source?.account_version ?? "",
  };
}
function validationLabel(value: boolean | null) {
  return value === true ? "通过" : value === false ? "未通过" : "未核对";
}

interface ActiveJob {
  source: SourceId;
  id: string;
  selection: Selection;
  timezone: string;
  conversationLabel: string;
  outputLabel: string;
}
interface AuthorityCheck { jobId: string; state: "pending" | "confirmed" | "failed" }

export function VerificationWizard({ setupOptions, api: suppliedApi }: { setupOptions: SetupOptions; api?: LocalApiClient }) {
  const ownedApi = useMemo(createClient, []);
  const api = suppliedApi ?? ownedApi;
  const defaults = useMemo(() => defaultNaturalRange(new Date()), []);
  const [sources, setSources] = useState<Source[]>([]);
  const [environment, setEnvironment] = useState<EnvironmentReport | null>(null);
  const [environmentError, setEnvironmentError] = useState(false);
  const [sourceId, setSourceId] = useState<SourceId | "">("");
  const [account, setAccount] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversation, setConversation] = useState("");
  const [conversationBusy, setConversationBusy] = useState(false);
  const [conversationMessage, setConversationMessage] = useState("");
  const [timezone, setTimezone] = useState("Asia/Shanghai");
  const [start, setStart] = useState(defaults.start); const [end, setEnd] = useState(defaults.end);
  const [root, setRoot] = useState("");
  const [prepared, setPrepared] = useState<(PreparedConsent & { selection: Selection; timezone: string }) | null>(null);
  const [actionBusy, setActionBusy] = useState(false); const [error, setError] = useState("");
  const [job, setJob] = useState<ActiveJob | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [authorityCheck, setAuthorityCheck] = useState<AuthorityCheck | null>(null);
  const [polling, setPolling] = useState(false);
  const [config, setConfig] = useState<SourceConfiguration | null>(null);
  const [configBusy, setConfigBusy] = useState(false); const [configMessage, setConfigMessage] = useState("");
  const [secretConsent, setSecretConsent] = useState<SecretConsent | null>(null);
  const [secretAllowed, setSecretAllowed] = useState(false);
  const [secretGrantExpiresAt, setSecretGrantExpiresAt] = useState<string | null>(null);
  const [secretPreparing, setSecretPreparing] = useState(false); const [secretBusy, setSecretBusy] = useState(false);
  const [secretMessage, setSecretMessage] = useState("");
  const [disconnectPrepared, setDisconnectPrepared] = useState(false);
  const [disconnectConfirmed, setDisconnectConfirmed] = useState(false);
  const [disconnectBusy, setDisconnectBusy] = useState(false);
  const [disconnectMessage, setDisconnectMessage] = useState("");
  const [generationScopeLocked, setGenerationScopeLocked] = useState(false);
  const secretInput = useRef<HTMLInputElement>(null);
  const epoch = useRef(0); const active = useRef(true); const secretLock = useRef(false); const sourceRequest = useRef(0);
  const source = sources.find(item => item.source_id === sourceId);
  const terminal = report ? TERMINAL_STATES.has(report.state) : false;
  const activeJob = !!job && !terminal;
  const scopeLocked = actionBusy || polling || configBusy || conversationBusy || secretPreparing || secretBusy || disconnectBusy || activeJob || generationScopeLocked;
  const secretGrantReady = !source?.secret_configured || (!!secretGrantExpiresAt && Date.parse(secretGrantExpiresAt) > Date.now());

  function clearSecretConsent() {
    setSecretConsent(null); setSecretAllowed(false); setSecretMessage("");
    if (secretInput.current) secretInput.current.value = "";
  }
  function invalidateScope() {
    epoch.current++; setPrepared(null); clearSecretConsent(); setSecretGrantExpiresAt(null); setJob(null); setReport(null); setAuthorityCheck(null); setPolling(false);
    setDisconnectPrepared(false); setDisconnectConfirmed(false);
  }
  const loadSources = useCallback(async () => {
    const ticket = ++sourceRequest.current;
    const values = await api.sources();
    if (!active.current || ticket !== sourceRequest.current) return null;
    setSources(values);
    return values;
  }, [api]);
  useEffect(() => {
    active.current = true;
    void loadSources().catch(() => { if (active.current) setError("无法读取数据源，请检查本地服务。"); });
    void api.environment().then(value => { if (active.current) setEnvironment(value); }).catch(() => { if (active.current) setEnvironmentError(true); });
    return () => { active.current = false; sourceRequest.current++; epoch.current++; if (secretInput.current) secretInput.current.value = ""; };
  }, [loadSources, api]);
  useEffect(() => {
    if (!environment || !sourceId) return;
    const detected = configurationFor(sourceId, sources.find(item => item.source_id === sourceId), environment);
    setConfig(current => {
      if (!current || current.source_id !== sourceId) return current;
      const next = {
        ...current,
        client_version: current.client_version || detected.client_version,
        tool_version: current.tool_version || detected.tool_version,
      };
      return next.client_version === current.client_version && next.tool_version === current.tool_version ? current : next;
    });
  }, [environment, sourceId, sources]);
  useEffect(() => {
    if (!job || !polling) return;
    let cancelled = false; let timer: ReturnType<typeof setTimeout> | undefined; let attempt = 0;
    const poll = async () => {
      try {
        const value = await api.job(job.source, job.id);
        if (cancelled) return;
        setReport(value);
        if (TERMINAL_STATES.has(value.state)) {
          const authorityEpoch = epoch.current; const authorityJobId = job.id;
          setPolling(false); setAuthorityCheck({ jobId: authorityJobId, state: "pending" });
          void loadSources().then(values => {
            if (active.current && authorityEpoch === epoch.current && values) setAuthorityCheck({ jobId: authorityJobId, state: "confirmed" });
          }).catch(() => {
            if (active.current && authorityEpoch === epoch.current) {
              setAuthorityCheck({ jobId: authorityJobId, state: "failed" });
              setError("任务已结束，但无法刷新来源权威状态；结果保持未验证。");
            }
          });
          return;
        }
        if (attempt >= POLL_DELAYS_MS.length) {
          setPolling(false); setError("自动查询已暂停；任务仍保留，可重新查询或取消。"); return;
        }
        timer = setTimeout(poll, POLL_DELAYS_MS[attempt++]);
      } catch {
        if (!cancelled) { setPolling(false); setError("无法读取验证状态；任务未自动重启，可重新查询或取消。"); }
      }
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [job, polling, api, loadSources]);

  function chooseSource(id: SourceId | "") {
    if (scopeLocked) return;
    invalidateScope(); setError(""); setConfigMessage(""); setDisconnectMessage(""); setSourceId(id);
    const selected = sources.find(item => item.source_id === id);
    setAccount(selected?.account_id ?? ""); setConfig(id ? configurationFor(id, selected, environment) : null);
    setConversation(""); setConversations([]); setConversationMessage("");
  }
  async function refresh() {
    if (scopeLocked) return;
    invalidateScope(); setConversation(""); setConversations([]);
    const revision = epoch.current; setActionBusy(true); setError("");
    try {
      const values = await loadSources();
      if (!values || revision !== epoch.current) return;
      const selected = values.find(item => item.source_id === sourceId);
      setAccount(selected?.account_id ?? "");
      if (sourceId) setConfig(configurationFor(sourceId, selected, environment));
    } catch { if (active.current && revision === epoch.current) setError("刷新失败，请检查本地服务。"); }
    finally { if (active.current && revision === epoch.current) setActionBusy(false); }
  }
  async function saveConfiguration() {
    if (!config || configBusy || scopeLocked || !Object.values(config).every(value => typeof value === "string" && value.trim())) return;
    invalidateScope(); setConversation(""); setConversations([]);
    const revision = epoch.current; const snapshot = { ...config }; setConfigBusy(true); setError(""); setConfigMessage("");
    try {
      await api.configure(snapshot);
      let values: Source[] | null;
      try { values = await loadSources(); }
      catch {
        if (active.current && revision === epoch.current) setConfigMessage("本机来源配置已保存，但状态刷新失败；不会自动重提配置，请手动刷新数据源。");
        return;
      }
      if (!values || revision !== epoch.current) return;
      const selected = values.find(item => item.source_id === snapshot.source_id);
      setAccount(selected?.account_id ?? "");
      setConfig(configurationFor(snapshot.source_id, selected, environment));
      setConfigMessage("本机来源配置已保存；这不会安装、启动或登录第三方工具，也不代表真实导出已验证。");
    } catch (value) {
      if (active.current && revision === epoch.current) setError(value instanceof ApiError && value.status === 401 ? "本机会话已过期；配置未自动重放，请再次确认后保存。" : "本机来源配置未保存，请核对固定端点与版本信息。");
    } finally { if (active.current && revision === epoch.current) setConfigBusy(false); }
  }

  async function readConversations() {
    if (!sourceId || !account || scopeLocked || !source || !secretGrantReady || ["blocked", "unsupported", "error", "unconfigured"].includes(source.status)) return;
    const usedSecretGrant = source.secret_configured;
    invalidateScope(); setConversations([]); setConversation(""); setConversationBusy(true); setConversationMessage(""); setError("");
    const revision = epoch.current; const sourceAtStart = sourceId; const accountAtStart = account;
    try {
      const values = await api.conversations(sourceAtStart, accountAtStart);
      if (active.current && revision === epoch.current) {
        const scopedValues = values.filter(item => item.account_id === accountAtStart);
        setConversations(scopedValues);
        setConversationMessage(scopedValues.length ? "已读取本机会话元数据；尚未读取消息正文。" : "本地工具未返回当前账号的可选会话；未读取消息正文。");
        if (usedSecretGrant) setSecretMessage("列出会话已消费本次 Token 授权；验证导出前需要重新授权。");
      }
    } catch { if (active.current && revision === epoch.current) {
      setError("无法读取会话；没有自动重试或改用其他来源。");
      if (usedSecretGrant) setSecretMessage("列出会话可能已消费本次 Token 授权；重试前需要重新授权。");
    } }
    finally { if (active.current && revision === epoch.current) setConversationBusy(false); }
  }

  const selection: Selection = { source_id: sourceId || "qq", account_id: account, conversation_selection_id: conversation,
    range_start: instant(start, timezone), range_end: instant(end, timezone), output_root_id: root };
  const disclosure = sourceId ? setupOptions.source_disclosures[sourceId] : null;
  const valid = !!source && !!sourceId && !!account && !!conversation && secretGrantReady &&
    conversations.some(item => item.conversation_selection_id === conversation && item.account_id === account) &&
    setupOptions.approved_output_roots.some(item => item.output_root_id === root) &&
    Date.parse(selection.range_end) > Date.parse(selection.range_start) && !!disclosure && !disclosure.blockers.length &&
    !!source.client_version && !!source.tool_version && !!source.account_version &&
    !["blocked", "unsupported", "error", "unconfigured"].includes(source.status);
  async function prepare() {
    if (!valid || !sourceId || scopeLocked) return;
    const revision = epoch.current; setActionBusy(true); setError("");
    try {
      const value = await api.prepare(sourceId, selection, timezone);
      if (active.current && revision === epoch.current) setPrepared({ ...value, selection, timezone });
    } catch { if (active.current && revision === epoch.current) setError("无法授权本次范围，请刷新数据源后重新确认。"); }
    finally { if (active.current && revision === epoch.current) setActionBusy(false); }
  }
  async function begin() {
    if (!prepared || !sourceId || scopeLocked) return;
    if (!Number.isFinite(Date.parse(prepared.expires_at)) || Date.parse(prepared.expires_at) <= Date.now()) { invalidateScope(); setError("授权已过期，请重新确认。"); return; }
    if (source?.secret_configured && (!secretGrantExpiresAt || Date.parse(secretGrantExpiresAt) <= Date.now())) { invalidateScope(); setError("本次 Token 使用授权已过期，请重新授权后再确认范围。"); return; }
    const revision = epoch.current; const sourceAtStart = sourceId; setActionBusy(true); setError("");
    try {
      const result = await api.start(sourceAtStart, prepared.selection, prepared.timezone, prepared.consent_challenge, prepared.disclosure);
      if (active.current && revision === epoch.current) {
        const selectedConversation = conversations.find(item => item.conversation_selection_id === prepared.selection.conversation_selection_id);
        const output = setupOptions.approved_output_roots.find(item => item.output_root_id === prepared.selection.output_root_id);
        setJob({ source: sourceAtStart, id: result.job_id, selection: prepared.selection, timezone: prepared.timezone,
          conversationLabel: selectedConversation ? conversationLabel(selectedConversation) : "已选会话",
          outputLabel: output?.label ?? "已批准目录" });
        setReport(null); setPrepared(null); setSecretGrantExpiresAt(null); setAuthorityCheck(null); setPolling(true);
      }
    } catch (value) {
      if (active.current && revision === epoch.current) {
        setPrepared(null);
        setError(value instanceof ApiError && [401, 403, 409].includes(value.status) ? "配置或授权已变化，请重新确认。" : "验证启动失败，请重新确认后重试。");
      }
    } finally { if (active.current && revision === epoch.current) setActionBusy(false); }
  }
  async function cancelJob() {
    if (!job || terminal || actionBusy) return;
    const current = job; setPolling(false); setActionBusy(true); setError("");
    try {
      await api.cancel(current.source, current.id);
      const value = await api.job(current.source, current.id);
      if (active.current && job === current) { setReport(value); setAuthorityCheck(null); }
    } catch { if (active.current) setError("取消状态未能确认；不会自动重启任务，请重新查询。"); }
    finally { if (active.current) setActionBusy(false); }
  }
  function resumePolling() {
    if (!job || terminal || polling || actionBusy) return;
    setError(""); setPolling(true);
  }

  async function prepareSecret(operation: "secret_set" | "secret_get" | "secret_delete") {
    if (!sourceId || !account || scopeLocked) return;
    const revision = epoch.current; setSecretPreparing(true); clearSecretConsent();
    try {
      const value = await api.secretConsent(sourceId, account, operation);
      if (active.current && revision === epoch.current) setSecretConsent(value);
    } catch { if (active.current && revision === epoch.current) setSecretMessage("无法取得本次秘密操作确认；没有读取或发送 Token。"); }
    finally { if (active.current && revision === epoch.current) setSecretPreparing(false); }
  }
  async function saveSecret() {
    if (!sourceId || !account || !secretConsent || secretConsent.disclosure.operation !== "secret_set" || !secretAllowed || secretLock.current || !secretInput.current?.value) return;
    if (Date.parse(secretConsent.expires_at) <= Date.now()) { clearSecretConsent(); setSecretMessage("秘密操作确认已过期；Token 未发送，请重新确认。"); return; }
    let value = secretInput.current.value;
    secretInput.current.value = ""; // Clear synchronously before the same-origin operation starts.
    const revision = epoch.current; const sourceAtStart = sourceId; const accountAtStart = account; const challenge = secretConsent.consent_challenge;
    secretLock.current = true; setSecretBusy(true); setSecretAllowed(false); setSecretMessage("");
    try {
      const pending = api.saveSecret(sourceAtStart, accountAtStart, challenge, value, secretConsent.disclosure);
      value = "";
      await pending;
      if (!active.current || revision !== epoch.current) return;
      setSecretConsent(null); setSecretGrantExpiresAt(null);
      try {
        const refreshed = await loadSources();
        if (active.current && revision === epoch.current) setSecretMessage(refreshed ?
          "Token 已保存至本机后端；页面只显示已保存状态。" :
          "Token 已保存，但非秘密状态刷新未完成；不会自动重发 Token，请手动刷新数据源。");
      } catch {
        if (active.current && revision === epoch.current) setSecretMessage("Token 已保存，但非秘密状态刷新失败；不会自动重发 Token，请手动刷新数据源。");
      }
    } catch { if (active.current && revision === epoch.current) { setSecretConsent(null); setSecretMessage("秘密操作未完成；不会自动重试或再次发送 Token。"); } }
    finally { value = ""; secretLock.current = false; if (active.current) setSecretBusy(false); }
  }
  async function authorizeSecretUse() {
    if (!sourceId || !account || !secretConsent || secretConsent.disclosure.operation !== "secret_get" || !secretAllowed || secretLock.current) return;
    if (Date.parse(secretConsent.expires_at) <= Date.now()) { clearSecretConsent(); setSecretMessage("秘密操作确认已过期；未授权读取 Token，请重新确认。"); return; }
    const revision = epoch.current; const sourceAtStart = sourceId; const accountAtStart = account;
    const challenge = secretConsent.consent_challenge; const expiresAt = secretConsent.expires_at;
    secretLock.current = true; setSecretBusy(true); setSecretAllowed(false); setSecretMessage("");
    try {
      await api.authorizeSecretUse(sourceAtStart, accountAtStart, challenge, secretConsent.disclosure);
      if (active.current && revision === epoch.current) {
        setSecretConsent(null); setSecretGrantExpiresAt(expiresAt);
        setSecretMessage("已授权下一次受保护来源操作读取已保存 Token；该授权仅限当前来源、账号、配置与本机会话，并会由列会话或验证任务单次消费。");
      }
    } catch {
      if (active.current && revision === epoch.current) { setSecretConsent(null); setSecretGrantExpiresAt(null); setSecretMessage("本次 Token 使用授权未完成；不会自动重试。"); }
    } finally { secretLock.current = false; if (active.current) setSecretBusy(false); }
  }

  async function deleteSecret() {
    if (!sourceId || !account || !secretConsent || secretConsent.disclosure.operation !== "secret_delete" || !secretAllowed || secretLock.current) return;
    if (Date.parse(secretConsent.expires_at) <= Date.now()) { clearSecretConsent(); setSecretMessage("秘密操作确认已过期；Token 未删除，请重新确认。"); return; }
    const revision = epoch.current; const sourceAtStart = sourceId; const accountAtStart = account;
    secretLock.current = true; setSecretBusy(true); setSecretAllowed(false); setSecretMessage("");
    try {
      await api.deleteSecret(sourceAtStart, accountAtStart, secretConsent.consent_challenge, secretConsent.disclosure);
      if (!active.current || revision !== epoch.current) return;
      setSecretConsent(null); setSecretGrantExpiresAt(null);
      await loadSources();
      if (active.current && revision === epoch.current) setSecretMessage("Token 已从本机后端秘密存储删除；上游工具缓存需按工具文档单独清理。");
    } catch { if (active.current && revision === epoch.current) { setSecretConsent(null); setSecretMessage("Token 删除未完成；不会自动重试。"); } }
    finally { secretLock.current = false; if (active.current) setSecretBusy(false); }
  }

  async function disconnectSource() {
    if (!sourceId || source?.status === "unconfigured" || source?.secret_configured || !disconnectConfirmed || disconnectBusy || scopeLocked) return;
    const revision = epoch.current; const sourceAtStart = sourceId;
    setDisconnectBusy(true); setDisconnectMessage("");
    try {
      await api.disconnectSource(sourceAtStart);
      if (!active.current || revision !== epoch.current) return;
      setPrepared(null); setJob(null); setReport(null); setAuthorityCheck(null); setPolling(false);
      setConversations([]); setConversation(""); setAccount(""); setSecretGrantExpiresAt(null);
      setDisconnectPrepared(false); setDisconnectConfirmed(false);
      const refreshed = await loadSources();
      if (active.current && revision === epoch.current) {
        const current = refreshed?.find(item => item.source_id === sourceAtStart);
        setConfig(configurationFor(sourceAtStart, current, environment));
        setDisconnectMessage("已断开本机来源配置。已导入的聊天快照、文档、导出文件与上游工具缓存均未删除。");
      }
    } catch { if (active.current && revision === epoch.current) setDisconnectMessage("断开未完成；请先结束活动任务并删除已保存 Token。"); }
    finally { if (active.current) setDisconnectBusy(false); }
  }

  const currentReportSource = report ? sources.find(item => item.source_id === report.source_id) : undefined;
  const reportBound = !!report && !!job && report.source_id === job.source &&
    Date.parse(report.range_start) === Date.parse(job.selection.range_start) &&
    Date.parse(report.range_end) === Date.parse(job.selection.range_end) && report.timezone === job.timezone &&
    currentReportSource?.account_id === job.selection.account_id;
  const authorityState = job && authorityCheck?.jobId === job.id ? authorityCheck.state : null;
  const trustedReal = reportBound && authorityState === "confirmed" && isTrustedRealReport(report, currentReportSource);
  const reportVerdict = report ? trustedReal ? "真实设备导出验证完成" : report.state === "complete" && report.evidence_class === "synthetic" ? "合成验证完成，未通过真实来源验收" :
    report.state === "complete" && authorityState === "pending" ? "任务完成，正在刷新来源权威状态" :
    report.state === "complete" && authorityState === "failed" ? "任务完成，但来源权威状态刷新失败，保持未验证" :
    report.state === "complete" ? "任务完成，但证据未达到真实来源验收标准" : `验证状态：${STATE_LABELS[report.state]}` : "";

  return <>
    {environment ? <section aria-label="本机安全检测" className="panel"><h2>本机安全检测</h2>
      <p>系统：{environment.os.system} · 版本：{environment.os.version} · 物理架构：{environment.os.physical_architecture} · 进程架构：{environment.os.process_architecture}{environment.os.rosetta_translated === true ? "（Rosetta）" : ""}</p>
      <dl className="source-meta">{(["qq", "wechat", "qce", "tracememo"] as const).map(key => {
        const metadata = environment.applications[key]; const label = { qq: "QQ", wechat: "微信", qce: "QCE", tracememo: "TraceMemo" }[key];
        return <div key={key}><dt>{label}</dt><dd>{metadata.installed ? `${metadata.version} · ${metadata.architecture}` : "未检测到"}</dd></div>;
      })}</dl>
      {environment.issues.map(issue => <p className="warning" key={issue.code}>{issue.message}（{issue.code}）</p>)}</section> :
      environmentError ? <p role="status">本机安全检测元数据暂不可用；不会据此猜测版本。</p> : null}
    <SourceSetup sources={sources} reports={reportBound && report && authorityState === "confirmed" ? [report] : []} onSourceChange={chooseSource} selectedSource={sourceId} disabled={scopeLocked} />
    {sourceId && config && <section aria-label="本机来源配置" className="panel">
      <h2>本机来源配置</h2>
      <p>仅保存连接所需的公开元数据；不会安装、启动或登录工具，不会提权、关闭 SIP、重签名客户端或降低系统保护。</p>
      <p>固定本机端点：<code>{config.base_url}</code></p>
      <div className="fields">
        <label>来源账号标识<input value={config.account_id} disabled={scopeLocked} onChange={event => setConfig({ ...config, account_id: event.target.value })} /></label>
        <label>{sourceId === "qq" ? "QQ 客户端版本" : "微信客户端版本"}<input value={config.client_version} disabled={scopeLocked} onChange={event => setConfig({ ...config, client_version: event.target.value })} /></label>
        <label>本地工具版本<input value={config.tool_version} disabled={scopeLocked} onChange={event => setConfig({ ...config, tool_version: event.target.value })} /></label>
        <label>账号数据版本<input value={config.account_version} disabled={scopeLocked} onChange={event => setConfig({ ...config, account_version: event.target.value })} /></label>
      </div>
      <button type="button" disabled={scopeLocked || !Object.values(config).every(value => typeof value === "string" && value.trim())} onClick={() => void saveConfiguration()}>保存本机来源配置</button>
      {configMessage && <p role="status">{configMessage}</p>}
      <section aria-label="断开本机来源" className="disconnect-source">
        <h3>断开本机来源</h3>
        <p>只忘记这个来源的本机连接配置和未使用授权；不删除已导入的聊天快照、生成文档、导出文件、上游工具缓存、QQ/微信数据或 macOS 权限。</p>
        <button type="button" disabled={scopeLocked || source?.status === "unconfigured" || !!source?.secret_configured}
          onClick={() => { setDisconnectPrepared(true); setDisconnectConfirmed(false); setDisconnectMessage(""); }}>准备断开本机来源</button>
        {source?.secret_configured && <p className="warning">请先在“上游访问令牌”中通过独立确认删除 Token。</p>}
        {disconnectPrepared && <div className="disconnect-confirmation">
          <label className="check"><input type="checkbox" checked={disconnectConfirmed} disabled={disconnectBusy}
            onChange={event => setDisconnectConfirmed(event.target.checked)} />确认忘记这个来源配置，并理解不会删除已导入或上游数据</label>
          <button type="button" disabled={!disconnectConfirmed || disconnectBusy} onClick={() => void disconnectSource()}>确认断开本机来源</button>
        </div>}
        {disconnectMessage && <p role="status">{disconnectMessage}</p>}
      </section>
    </section>}
    <section aria-label="窄范围验证" className="panel" aria-busy={actionBusy || polling}>
      <h2>验证一个小范围</h2><p>只读取选定范围并保存至已批准的本地位置。不会上传聊天，也不会启用 AI。</p>
      <div className="fields">
        <label>数据源<select value={sourceId} disabled={scopeLocked} onChange={event => chooseSource(event.target.value as SourceId | "")}><option value="">请选择数据源</option><option value="qq">QQ</option><option value="wechat">微信</option></select></label>
        <label>来源账号<select value={account} disabled={scopeLocked} onChange={event => { invalidateScope(); setAccount(event.target.value); setConversation(""); setConversations([]); setConversationMessage(""); }}><option value="">请选择来源账号</option>{source?.account_id && <option value={source.account_id}>{source.account_id}</option>}</select></label>
        <label>聊天会话<select value={conversation} disabled={scopeLocked} onChange={event => { invalidateScope(); setConversation(event.target.value); }}><option value="">请选择聊天会话</option>{conversations.map(item => <option key={item.conversation_selection_id} value={item.conversation_selection_id}>{conversationLabel(item)}</option>)}</select></label>
        <label>显示时区<select value={timezone} disabled={scopeLocked} onChange={event => { invalidateScope(); setTimezone(event.target.value); }}><option value="Asia/Shanghai">Asia/Shanghai (UTC+08:00)</option><option value="UTC">UTC</option></select></label>
        <label>开始时间<input type="datetime-local" step="1" value={start} disabled={scopeLocked} onChange={event => { invalidateScope(); setStart(event.target.value); }} /></label>
        <label>结束时间（不包含）<input type="datetime-local" step="1" value={end} disabled={scopeLocked} onChange={event => { invalidateScope(); setEnd(event.target.value); }} /></label>
        <label>保存位置<select value={root} disabled={scopeLocked} onChange={event => { invalidateScope(); setRoot(event.target.value); }}><option value="">请选择批准目录</option>{setupOptions.approved_output_roots.map(item => <option key={item.output_root_id} value={item.output_root_id}>{item.label}</option>)}</select></label>
      </div>
      {sourceId && disclosure && <section aria-label="读取会话前的来源披露" className="access-disclosure"><h3>读取会话前的来源披露</h3>
        <p>来源工具：{disclosure.tool} · 修订：{disclosure.revision} · 固定端点：<code>{sourceBaseUrls[sourceId]}</code></p>
        <p>点击后才连接已配置的本机工具并读取会话名称、类型与不透明选择标识；不会读取消息正文，不会安装、启动、登录、提权或降低系统保护。</p>
        <details><summary>查看完整来源披露与风险</summary>
          <p>摘要（SHA-256）：<code>{disclosure.hash}</code></p>
          <ul>{disclosure.risks.map(risk => <li key={risk}><code>{risk}</code></li>)}</ul>
          {Object.entries(sectionLabels).map(([key, label]) => <div key={key}><strong>{label}</strong><p>{disclosure.sections[key as keyof typeof sectionLabels]}</p></div>)}
          {disclosure.blockers.map(blocker => <p className="warning" key={blocker}>{blocker}</p>)}</details>
        <button type="button" disabled={!account || scopeLocked || !secretGrantReady || !!disclosure.blockers.length || !source || ["blocked", "unsupported", "error", "unconfigured"].includes(source.status)} onClick={() => void readConversations()}>读取本机会话列表</button>
        {source?.secret_configured && !secretGrantReady && <p>读取会话前，请在“上游访问令牌”中完成下一次 Token 使用确认。</p>}
        {conversationMessage && <p role="status">{conversationMessage}</p>}
      </section>}
      {!setupOptions.approved_output_roots.length && <p role="alert">尚无批准的保存目录，请先完成本地目录配置。</p>}
      {error && <p role="alert">{error}</p>}
      <div className="actions"><button type="button" disabled={scopeLocked} onClick={() => void refresh()}>刷新数据源</button><button type="button" disabled={!valid || scopeLocked} onClick={() => void prepare()}>查看操作确认</button>
        {job && !terminal && !polling && <button type="button" disabled={actionBusy} onClick={resumePolling}>重新查询任务</button>}
        {job && !terminal && <button type="button" disabled={actionBusy} onClick={() => void cancelJob()}>取消当前任务</button>}</div>
      {prepared && source && <ConsentCard source={source} selection={prepared.selection} timezone={prepared.timezone} disclosure={prepared.disclosure}
        scopeFingerprint={prepared.scope_fingerprint} consentChallenge={prepared.consent_challenge} expiresAt={prepared.expires_at}
        onConfirm={() => void begin()} onDecline={() => { epoch.current++; setPrepared(null); }} busy={actionBusy} />}
      {job && <section aria-label="验证结果"><h3>验证结果</h3><p role="status">{report ? reportVerdict : "验证状态：等待授权"}</p>
        <p>来源：{job.source === "qq" ? "QQ" : "微信"} · 账号：{job.selection.account_id} · 会话：{job.conversationLabel}</p>
        <p>范围：{job.selection.range_start} 至 {job.selection.range_end}（结束不包含） · 保存位置：{job.outputLabel}</p>
        {report?.evidence_class === "synthetic" && <p>本次仅有合成证据，不代表 QQ 或微信真实来源验收。</p>}
        <p>阶段：{report ? STATE_LABELS[report.state] : STATE_LABELS.pending_authorization} · 记录数：{report?.count ?? "未知"} · 哈希核对：{validationLabel(report?.hash_ok ?? null)} · 范围核对：{validationLabel(report?.range_ok ?? null)}</p>
        <p>完整性：{report?.completeness ?? "未核对"} · 错误代码：{report?.error_code ?? "未提供"}</p>
      </section>}
    </section>
    <ChatImportPanel api={api} outputRootId={root} />
    {sourceId && <ChatSearchPanel
      api={api} sourceId={sourceId} account={account} conversations={conversations}
      rangeStart={selection.range_start} rangeEnd={selection.range_end} timezone={timezone}
      outputRootId={root} disabled={scopeLocked} secretConfigured={!!source?.secret_configured}
      secretGrantReady={secretGrantReady} onSecretGrantConsumed={() => setSecretGrantExpiresAt(null)}
      onGenerationScopeLockChange={setGenerationScopeLocked}
    />}
    {sourceId && <section aria-label="本机令牌" className="panel" aria-busy={secretPreparing || secretBusy}><h2>上游访问令牌</h2>
      <p>仅发送给同源本机后端保存；不会显示已保存的值。秘密授权与导出授权分开。</p>
      <p>令牌状态：{source?.secret_configured ? "已保存" : "未保存"}</p>
      {secretGrantExpiresAt && <p>下一次 Token 使用授权有效期：<time dateTime={secretGrantExpiresAt}>{secretGrantExpiresAt}</time>；列出会话或创建验证任务后即单次消费。</p>}
      <div className="actions"><button type="button" disabled={!account || scopeLocked} onClick={() => void prepareSecret("secret_set")}>查看令牌保存确认</button>
        {source?.secret_configured && <button type="button" disabled={!account || scopeLocked || secretGrantReady} onClick={() => void prepareSecret("secret_get")}>查看下一次 Token 使用确认</button>}
        {source?.secret_configured && <button type="button" disabled={!account || scopeLocked} onClick={() => void prepareSecret("secret_delete")}>查看 Token 删除确认</button>}</div>
      {secretConsent && <div className="secret-consent">
        <p>来源：{secretConsent.disclosure.source_id === "qq" ? "QQ" : "微信"} · 工具：{secretConsent.disclosure.tool} · 操作：{secretConsent.disclosure.operation === "secret_set" ? "保存 Token" : secretConsent.disclosure.operation === "secret_get" ? "读取已保存 Token" : "删除已保存 Token"} · 秘密后端：{secretConsent.disclosure.secret_backend}</p>
        <p>披露修订：{secretConsent.disclosure.revision} · 摘要（SHA-256）：<code>{secretConsent.disclosure.hash}</code></p>
        {Object.entries(secretSectionLabels).map(([key, label]) => <section key={key} aria-label={label}><h3>{label}</h3><p>{secretConsent.disclosure.sections[key as keyof typeof secretSectionLabels]}</p></section>)}
        <div aria-label="秘密操作风险代码"><h3>秘密操作风险代码</h3><ul>{secretConsent.disclosure.risks.map(risk => <li key={risk}><code>{risk}</code></li>)}</ul></div>
        <p>确认有效期：<time dateTime={secretConsent.expires_at}>{secretConsent.expires_at}</time></p>
        {secretConsent.disclosure.operation === "secret_set" && <label>上游 API Token<input ref={secretInput} type="password" autoComplete="off" spellCheck={false} disabled={secretBusy} /></label>}
        <label className="check"><input type="checkbox" checked={secretAllowed} disabled={secretBusy} onChange={event => setSecretAllowed(event.target.checked)} />{secretConsent.disclosure.operation === "secret_set" ? "授权本次保存 Token" : secretConsent.disclosure.operation === "secret_get" ? "授权本次读取已保存 Token" : "授权本次删除已保存 Token"}</label>
        <button type="button" disabled={!secretAllowed || secretBusy} onClick={() => void (secretConsent.disclosure.operation === "secret_set" ? saveSecret() : secretConsent.disclosure.operation === "secret_get" ? authorizeSecretUse() : deleteSecret())}>{secretConsent.disclosure.operation === "secret_set" ? "保存 Token" : secretConsent.disclosure.operation === "secret_get" ? "授权本次使用 Token" : "删除已保存 Token"}</button></div>}
      {secretMessage && <p role="status">{secretMessage}</p>}
    </section>}
  </>;
}
