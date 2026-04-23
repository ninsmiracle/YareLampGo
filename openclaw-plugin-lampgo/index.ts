import { Type } from "@sinclair/typebox";
import type { OpenClawPluginModule } from "openclaw/plugin-sdk";

type TextContent = { type: "text"; text: string };

function getLampgoApiBase(pluginConfig: Record<string, unknown> | null | undefined): string {
  // The gateway URL is supplied exclusively via pluginConfig.lampgoApiBase,
  // which `lampgo install-openclaw` writes into ~/.openclaw/openclaw.json at
  // install time based on the lampgo daemon port. This avoids any runtime
  // environment reads that would trip OpenClaw's plugin-sandbox scanner.
  const configured = pluginConfig?.["lampgoApiBase"];
  if (typeof configured === "string" && configured.trim()) {
    return configured.trim().replace(/\/+$/, "");
  }
  return "http://127.0.0.1:8420";
}

function getLampgoPluginToken(pluginConfig: Record<string, unknown> | null | undefined): string {
  // Shared secret for memory/persona write ops. Issued once by
  // `lampgo install-openclaw` and stored both in ~/.lampgo/credentials.json
  // and in this plugin's config block. Required on PUT/POST against
  // /api/persona and /api/memory.
  const configured = pluginConfig?.["lampgoPluginToken"];
  if (typeof configured === "string" && configured.trim()) {
    return configured.trim();
  }
  return "";
}

function authHeaders(token: string): Record<string, string> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (token) headers["x-lampgo-plugin-token"] = token;
  return headers;
}

async function postJson<T>(url: string, body: unknown, token = ""): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`lampgo http ${res.status}: ${text || res.statusText}`);
  }
  return (await res.json()) as T;
}

async function putJson<T>(url: string, body: unknown, token = ""): Promise<T> {
  const res = await fetch(url, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`lampgo http ${res.status}: ${text || res.statusText}`);
  }
  return (await res.json()) as T;
}

async function getJson<T>(url: string, token = ""): Promise<T> {
  const res = await fetch(url, { method: "GET", headers: authHeaders(token) });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`lampgo http ${res.status}: ${text || res.statusText}`);
  }
  return (await res.json()) as T;
}

function toolOk(text: string) {
  return {
    content: [{ type: "text" as const, text } satisfies TextContent],
    details: null,
  };
}

const plugin: OpenClawPluginModule = {
  id: "lampgo",
  name: "LampGo",
  description: "Bridge OpenClaw tools to a local lampgo daemon via HTTP.",

  register(api) {
    const getBase = () => getLampgoApiBase(api.pluginConfig as Record<string, unknown> | null | undefined);
    const getToken = () => getLampgoPluginToken(api.pluginConfig as Record<string, unknown> | null | undefined);

    api.registerTool({
      name: "lampgo_move",
      label: "Move lampgo joints",
      description:
        "Move lampgo joints via the move_to skill. Use explicit joint names and target angles (degrees).",
      parameters: Type.Object({
        joints: Type.Record(
          Type.String({ description: "joint name" }),
          Type.Number({ description: "target angle (deg)" }),
          { description: "Joint targets, partial ok." },
        ),
        wait: Type.Optional(Type.Boolean({ description: "Wait for completion (default true)." })),
      }),
      async execute(_toolCallId, params) {
        const base = getBase();
        const result = await postJson<{ ok: boolean; error?: string }>(`${base}/api/invoke`, {
          skill_id: "move_to",
          params: params.joints,
          wait: params.wait ?? true,
        });
        if (!result.ok) throw new Error(result.error || "lampgo invoke failed");
        return toolOk("ok");
      },
    });

    api.registerTool({
      name: "lampgo_expression",
      label: "Set lampgo LED expression",
      description: "Set lampgo LED expression.",
      parameters: Type.Object({
        mode: Type.String({ description: "LED expression mode key, e.g. happy/heart/working." }),
        wait: Type.Optional(Type.Boolean({ description: "Wait for completion (default true)." })),
      }),
      async execute(_toolCallId, params) {
        const base = getBase();
        const result = await postJson<{ ok: boolean; error?: string }>(`${base}/api/invoke`, {
          skill_id: "set_expression",
          params: { mode: params.mode },
          wait: params.wait ?? true,
        });
        if (!result.ok) throw new Error(result.error || "lampgo invoke failed");
        return toolOk("ok");
      },
    });

    api.registerTool({
      name: "lampgo_play",
      label: "Play lampgo motion clip",
      description: "Play a pre-recorded lampgo motion clip.",
      parameters: Type.Object({
        name: Type.String({ description: "Recording name (stem of CSV), e.g. nod/dance." }),
        wait: Type.Optional(Type.Boolean({ description: "Wait for completion (default true)." })),
      }),
      async execute(_toolCallId, params) {
        const base = getBase();
        const result = await postJson<{ ok: boolean; error?: string }>(`${base}/api/invoke`, {
          skill_id: "play_recording",
          params: { name: params.name },
          wait: params.wait ?? true,
        });
        if (!result.ok) throw new Error(result.error || "lampgo invoke failed");
        return toolOk("ok");
      },
    });

    api.registerTool({
      name: "lampgo_status",
      label: "Get lampgo device status",
      description: "Get current lampgo device status snapshot.",
      parameters: Type.Object({}),
      async execute() {
        const base = getBase();
        const data = await getJson<unknown>(`${base}/api/status`);
        return toolOk(JSON.stringify(data, null, 2));
      },
    });

    api.registerTool({
      name: "lampgo_sensor_context",
      label: "Get lampgo sensor context",
      description: "Get aggregated sensor context (camera/voice config) from lampgo.",
      parameters: Type.Object({}),
      async execute() {
        const base = getBase();
        const data = await getJson<unknown>(`${base}/api/sensor/context`);
        return toolOk(JSON.stringify(data, null, 2));
      },
    });

    api.registerTool({
      name: "lampgo_camera_snap",
      label: "Capture lampgo camera snapshot",
      description: "Capture a camera snapshot from lampgo (returns base64 data URL).",
      parameters: Type.Object({}),
      async execute() {
        const base = getBase();
        const data = await getJson<unknown>(`${base}/api/camera/snap`);
        return toolOk(JSON.stringify(data, null, 2));
      },
    });

    api.registerTool({
      name: "lampgo_recordings",
      label: "List lampgo recordings",
      description: "List available pre-recorded motion clips.",
      parameters: Type.Object({}),
      async execute() {
        const base = getBase();
        const data = await getJson<unknown>(`${base}/api/recordings`);
        return toolOk(JSON.stringify(data, null, 2));
      },
    });

    api.registerTool({
      name: "lampgo_save_recording",
      label: "Save lampgo recording",
      description:
        "Save a new motion recording (CSV) to lampgo's recordings directory so it can be played back immediately. " +
        "Optionally register a natural-language alias so users can trigger it by name. " +
        "CSV format: header row must be: timestamp,base_yaw.pos,base_pitch.pos,elbow_pitch.pos,wrist_roll.pos,wrist_pitch.pos — " +
        "timestamp is seconds (can start at 0, increment by 1/fps per frame), all angle values in degrees. " +
        "Example row: 0.000,0,-45,65,0,5",
      parameters: Type.Object({
        name: Type.String({ description: "Identifier for the recording (alphanumeric/dash/underscore, e.g. bow_greeting)." }),
        csv: Type.String({ description: "Full CSV content of the recording." }),
        alias: Type.Optional(Type.String({ description: "Natural-language trigger phrase to register in aliases.json (e.g. '鞠躬问好')." })),
      }),
      async execute(_toolCallId, params) {
        const base = getBase();
        const result = await postJson<{ ok: boolean; result?: { name: string; path: string; alias: string | null }; error?: string }>(
          `${base}/api/recordings/save`,
          { name: params.name, csv: params.csv, alias: params.alias ?? "" },
        );
        if (!result.ok) throw new Error(result.error || "save recording failed");
        const r = result.result!;
        return toolOk(`Saved: ${r.path}${r.alias ? `\nAlias registered: "${r.alias}" → ${r.name}` : ""}`);
      },
    });

    api.registerTool({
      name: "lampgo_get_persona",
      label: "Read lampgo persona",
      description:
        "Read lampgo's persona markdown files (SOUL / AGENTS / PROFILE). Useful for OpenClaw agents that want to " +
        "mirror the lamp's identity, tone, or user profile.",
      parameters: Type.Object({
        which: Type.Optional(
          Type.Union(
            [
              Type.Literal("SOUL"),
              Type.Literal("AGENTS"),
              Type.Literal("PROFILE"),
              Type.Literal("all"),
            ],
            { description: "Persona file to read, or 'all' (default)." },
          ),
        ),
      }),
      async execute(_toolCallId, params) {
        const base = getBase();
        const which = (params.which ?? "all").toString();
        if (which === "all") {
          const data = await getJson<unknown>(`${base}/api/persona`);
          return toolOk(JSON.stringify(data, null, 2));
        }
        const data = await getJson<unknown>(`${base}/api/persona/${encodeURIComponent(which)}`);
        return toolOk(JSON.stringify(data, null, 2));
      },
    });

    api.registerTool({
      name: "lampgo_get_memory",
      label: "Read lampgo memory",
      description:
        "Read lampgo's memory. date='core' returns MEMORY.md; date='YYYY-MM-DD' or 'today' returns that day's journal; " +
        "no date returns the list of available dates + today's journal.",
      parameters: Type.Object({
        date: Type.Optional(
          Type.String({ description: "'core', 'today', or an ISO date (YYYY-MM-DD)." }),
        ),
      }),
      async execute(_toolCallId, params) {
        const base = getBase();
        const date = (params.date ?? "").toString().trim();
        if (date === "core") {
          const data = await getJson<unknown>(`${base}/api/memory/core`);
          return toolOk(JSON.stringify(data, null, 2));
        }
        const url = date
          ? `${base}/api/memory/daily?date=${encodeURIComponent(date)}`
          : `${base}/api/memory/daily`;
        const data = await getJson<unknown>(url);
        return toolOk(JSON.stringify(data, null, 2));
      },
    });

    api.registerTool({
      name: "lampgo_save_memory",
      label: "Write lampgo memory",
      description:
        "Append bullet(s) to lampgo's daily memory. If promote=true, also upsert the bullet(s) into the permanent " +
        "core MEMORY.md. Requires the plugin token configured at install time.",
      parameters: Type.Object({
        bullets: Type.Array(Type.String({ description: "Single-line fact to remember." })),
        date: Type.Optional(
          Type.String({ description: "ISO date (YYYY-MM-DD). Omit for today." }),
        ),
        promote: Type.Optional(
          Type.Boolean({ description: "Also write to core MEMORY.md (default false)." }),
        ),
      }),
      async execute(_toolCallId, params) {
        const base = getBase();
        const token = getToken();
        if (!token) {
          throw new Error(
            "lampgo_save_memory requires a plugin token. Run `lampgo install-openclaw --yes` to refresh the integration.",
          );
        }
        const bullets = (params.bullets ?? []).filter((b) => typeof b === "string" && b.trim());
        if (!bullets.length) throw new Error("bullets must be a non-empty array of strings");
        const result = await postJson<{ ok: boolean; result?: { path: string; promoted: boolean }; error?: string }>(
          `${base}/api/memory/daily`,
          {
            bullets,
            date: params.date ?? undefined,
            promote: params.promote ?? false,
          },
          token,
        );
        if (!result.ok) throw new Error(result.error || "save memory failed");
        const r = result.result!;
        return toolOk(`Saved to ${r.path}${r.promoted ? " (also promoted to MEMORY.md)" : ""}`);
      },
    });

    api.registerTool({
      name: "lampgo_save_persona",
      label: "Write lampgo persona",
      description:
        "Overwrite one of lampgo's persona markdown files (SOUL / AGENTS / PROFILE) in ~/.lampgo/. " +
        "IMPORTANT: This writes to the LAMP's files, not OpenClaw's own persona — when the user (via the lamp) " +
        "says things like 'remember to call me XXX' or 'update my profile', edit lampgo's PROFILE.md here " +
        "instead of using generic file-write tools on ~/.openclaw/. Requires the plugin token configured at install time.",
      parameters: Type.Object({
        which: Type.Union(
          [
            Type.Literal("SOUL"),
            Type.Literal("AGENTS"),
            Type.Literal("PROFILE"),
          ],
          { description: "Which persona file to overwrite." },
        ),
        content: Type.String({ description: "Full new markdown content for the file." }),
      }),
      async execute(_toolCallId, params) {
        const base = getBase();
        const token = getToken();
        if (!token) {
          throw new Error(
            "lampgo_save_persona requires a plugin token. Run `lampgo install-openclaw --yes` to refresh the integration.",
          );
        }
        const which = params.which;
        const content = params.content ?? "";
        const result = await putJson<{ ok: boolean; result?: { name: string; bytes: number }; error?: string }>(
          `${base}/api/persona/${encodeURIComponent(which)}`,
          { content },
          token,
        );
        if (!result.ok) throw new Error(result.error || "save persona failed");
        const r = result.result!;
        return toolOk(`Saved ~/.lampgo/${r.name}.md (${r.bytes} bytes).`);
      },
    });

    api.registerTool({
      name: "lampgo_ask_user",
      label: "Ask user via lampgo",
      description: "Ask the user via lampgo (TTS/Web UI) and wait for a reply.",
      parameters: Type.Object({
        question: Type.String(),
        options: Type.Optional(Type.Array(Type.String())),
        timeout_s: Type.Optional(Type.Number({ description: "Timeout seconds (default 120)." })),
        request_id: Type.Optional(Type.String({ description: "Optional request id for correlation." })),
      }),
      async execute(_toolCallId, params) {
        const base = getBase();
        const result = await postJson<unknown>(`${base}/api/openclaw/ask`, {
          question: params.question,
          options: params.options ?? [],
          timeout_s: params.timeout_s ?? 120,
          request_id: params.request_id ?? "",
        });
        return toolOk(JSON.stringify(result, null, 2));
      },
    });
  },
};

export default plugin;
