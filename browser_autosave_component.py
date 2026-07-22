"""使用 Streamlit Components v2 接入浏览器 IndexedDB。"""

from __future__ import annotations

from types import SimpleNamespace

import streamlit as st


AUTOSAVE_DELAY_MS = 1000

_AUTOSAVE_JS = r"""
const DB_NAME = "ai_resume_assistant";
const DB_VERSION = 1;
const STORE_NAME = "workspace";
const RECORD_KEY = "latest";
const SAVE_DELAY = 1000;

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STORE_NAME)) {
        database.createObjectStore(STORE_NAME);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function readRecord() {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readonly");
    const request = transaction.objectStore(STORE_NAME).get(RECORD_KEY);
    request.onsuccess = () => resolve(request.result ?? null);
    request.onerror = () => reject(request.error);
    transaction.oncomplete = () => database.close();
  });
}

async function writeRecord(record) {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).put(record, RECORD_KEY);
    transaction.oncomplete = () => {
      database.close();
      resolve();
    };
    transaction.onerror = () => reject(transaction.error);
  });
}

async function clearRecord() {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).delete(RECORD_KEY);
    transaction.oncomplete = () => {
      database.close();
      resolve();
    };
    transaction.onerror = () => reject(transaction.error);
  });
}

function clone(value) {
  return value ? JSON.parse(JSON.stringify(value)) : null;
}

function findResumeForm() {
  return Array.from(document.querySelectorAll('[data-testid="stForm"]')).find((form) => {
    const text = form.textContent || "";
    return text.includes("保存主简历并预览") || text.includes("保存岗位版本并预览");
  });
}

function fieldValue(form, ariaLabel) {
  const element = form?.querySelector(`[aria-label="${ariaLabel}"]`);
  return element && typeof element.value === "string" ? element.value : "";
}

function collectDraft(runtime) {
  const form = findResumeForm();
  if (!form) return null;
  const current = runtime.latestSnapshot?.draft || {};
  return {
    name: fieldValue(form, "姓名 *"),
    phone: "",
    email: "",
    city: "",
    target_role: fieldValue(form, "目标岗位 *"),
    job_description: fieldValue(form, "岗位要求"),
    summary: fieldValue(form, "自我介绍"),
    education: fieldValue(form, "教育经历"),
    work_experience: fieldValue(form, "工作或实习经历"),
    project_experience: fieldValue(form, "项目经历"),
    skills: fieldValue(form, "技能与证书"),
    template_id: current.template_id || "business_blue",
    photo_base64: runtime.pendingPhotoBase64 ?? current.photo_base64 ?? "",
  };
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      resolve(result.includes(",") ? result.split(",", 2)[1] : "");
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export default function(component) {
  const { data, setStateValue, setTriggerValue } = component;
  const runtime = window.__aiResumeIndexedDbRuntime || {
    restoreSent: false,
    handledCommand: null,
    timer: null,
    pendingPhotoBase64: null,
    latestSnapshot: null,
  };
  window.__aiResumeIndexedDbRuntime = runtime;
  runtime.latestSnapshot = clone(data?.snapshot) || runtime.latestSnapshot;

  const save = async (captureDraft) => {
    const record = clone(runtime.latestSnapshot) || {
      schema_version: 1,
      master_resume: null,
      job_versions: {},
      workspace_mode: "master",
      active_job_version_id: null,
    };
    if (captureDraft) {
      const draft = collectDraft(runtime);
      if (!draft) return;
      record.draft = draft;
      record.draft_context = {
        workspace_mode: record.workspace_mode || "master",
        active_job_version_id: record.active_job_version_id || null,
      };
      record.draft_captured = true;
    }
    const hasData = Boolean(
      record.master_resume ||
      record.draft_captured ||
      Object.keys(record.job_versions || {}).length
    );
    if (!hasData) return;
    record.saved_at = new Date().toISOString();
    await writeRecord(record);
  };

  const scheduleSave = (captureDraft = true) => {
    if (runtime.timer) window.clearTimeout(runtime.timer);
    runtime.timer = window.setTimeout(() => {
      save(captureDraft).catch(() => {});
    }, SAVE_DELAY);
  };

  const handleInput = async (event) => {
    const form = findResumeForm();
    if (!form || !form.contains(event.target)) return;
    if (event.target instanceof HTMLInputElement && event.target.type === "file") {
      const file = event.target.files?.[0];
      if (file && file.size <= 5 * 1024 * 1024) {
        try {
          runtime.pendingPhotoBase64 = await fileToBase64(file);
        } catch (_) {
          runtime.pendingPhotoBase64 = null;
        }
      }
    }
    scheduleSave(true);
  };

  const handleVisibility = () => {
    if (document.visibilityState === "hidden" && runtime.timer) {
      window.clearTimeout(runtime.timer);
      runtime.timer = null;
      save(true).catch(() => {});
    }
  };

  document.addEventListener("input", handleInput, true);
  document.addEventListener("change", handleInput, true);
  document.addEventListener("visibilitychange", handleVisibility);

  if (!runtime.restoreSent) {
    runtime.restoreSent = true;
    readRecord()
      .then((record) => {
        if (record) setStateValue("restored", record);
      })
      .catch(() => setStateValue("storage_error", "浏览器不允许使用 IndexedDB。"));
  }

  const command = data?.command;
  if (command?.action === "clear" && command.nonce !== runtime.handledCommand) {
    runtime.handledCommand = command.nonce;
    if (runtime.timer) window.clearTimeout(runtime.timer);
    runtime.latestSnapshot = null;
    runtime.pendingPhotoBase64 = null;
    clearRecord()
      .then(() => setTriggerValue("cleared", command.nonce))
      .catch(() => setStateValue("storage_error", "无法清除浏览器自动保存数据。"));
  } else if (runtime.latestSnapshot) {
    scheduleSave(false);
  }

  return () => {
    document.removeEventListener("input", handleInput, true);
    document.removeEventListener("change", handleInput, true);
    document.removeEventListener("visibilitychange", handleVisibility);
    if (runtime.timer) {
      window.clearTimeout(runtime.timer);
      runtime.timer = null;
    }
  };
}
"""


_AUTOSAVE_COMPONENT = None


def mount_browser_autosave(snapshot: dict, command: dict | None = None):
    """挂载无可见高度的 IndexedDB 同步组件。"""
    global _AUTOSAVE_COMPONENT
    if _AUTOSAVE_COMPONENT is None:
        _AUTOSAVE_COMPONENT = st.components.v2.component(
            "resume_indexeddb_autosave",
            js=_AUTOSAVE_JS,
        )
    try:
        return _AUTOSAVE_COMPONENT(
            data={"snapshot": snapshot, "command": command},
            default={"restored": None, "storage_error": None},
            on_restored_change=lambda: None,
            on_storage_error_change=lambda: None,
            on_cleared_change=lambda: None,
            key="resume_browser_autosave",
            width="stretch",
            height=0,
        )
    except ValueError as error:
        if "is not registered" not in str(error):
            raise
        return SimpleNamespace(
            restored=None,
            storage_error=None,
            cleared=None,
        )
