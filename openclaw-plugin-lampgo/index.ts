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
        mode: Type.String({ description: "LED expression mode key, e.g. smiley/heart/focused/wink/myu7gt." }),
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

    // ------------------------------------------------------------------
    // User-authored composed skills
    // ------------------------------------------------------------------
    // These three tools let OpenClaw crystallise a *new skill* (not just a
    // motion recording) into lampgo's registry.  The saved definition is a
    // JSON sequence of existing factory skills — OpenClaw reads what's
    // available via lampgo_status / GET /api/skills, drafts the sequence,
    // writes it through lampgo_save_skill, and from the next turn onward
    // that skill appears to the fast-path LLM agent and the Web UI as a
    // first-class skill alongside factory built-ins.
    //
    // Safety rails are enforced on the lampgo side (see
    // ``lampgo/skills/loader.py``):
    //   • skill_id may not shadow a factory skill
    //   • each step.skill_id must reference a factory skill (no
    //     composed-calls-composed — eliminates recursion risk)
    //   • ``estop`` is never allowed in a step
    //   • total step count is capped (default 20)
    //
    // Design note: we deliberately expose `lampgo_save_skill` as a single
    // upsert (not separate create/update) — OpenClaw's reasoning does
    // better when it doesn't have to pre-check existence.  The "updated"
    // flag in the response tells downstream UI whether the call edited
    // an existing skill or created a fresh one.

    api.registerTool({
      name: "lampgo_save_skill",
      label: "Save lampgo composed skill",
      description:
        "Create or update a user composed skill in lampgo.  A skill is an ordered list of steps; " +
        "each step is either (a) a call to an existing factory skill, or (b) a custom joint-trajectory " +
        "described by keyframes.  Once saved, the skill appears in lampgo's skill registry and the Web " +
        "UI's '我的技能' section immediately — no daemon restart needed.  " +
        "Call this when the user asks for a repeatable routine or 'teach me a new trick'-style request.\n\n" +
        "STEP SHAPES — each step object has *exactly one* of:\n" +
        "  • `skill_id` (+ optional `params`): invoke a factory skill.  Factory ids: move_to, nod, headshake, " +
        "look_at, idle_sway, dance, set_expression, play_recording, return_safe.  (`estop` is forbidden.)  " +
        "String params support `{placeholder}` substitution from the outer skill's declared parameters.\n" +
        "  • `trajectory`: play a custom keyframe sequence.  Use this ONLY when no factory skill matches " +
        "the motion shape — e.g. a weird bobbing pattern, a pose the robot should hold, or a choreo that " +
        "mixes multiple joints in a way nod/dance/etc. can't express.  Prefer factory-skill steps when " +
        "they suffice: they encode carefully tuned velocity/ease profiles.\n\n" +
        "TRAJECTORY SAFETY — each waypoint joint value must be within hardware limits: " +
        "base_yaw ∈ [-150, 150], base_pitch ∈ [-100, 65], elbow_pitch ∈ [-90, 100], " +
        "wrist_roll ∈ [-75, 75], wrist_pitch ∈ [-45, 100].  Values outside these ranges make the save fail.  " +
        "Segment durations that would require unsafe joint velocity are auto-extended at runtime (not rejected).  " +
        "Interpolation is whitelisted: linear, ease_in_out_cubic (default), ease_in_out_quad, ease_out_cubic, " +
        "ease_in_cubic, ease_out_back.  Caps: ≤ 50 waypoints, ≤ 30 s total duration, fps ∈ [10, 100].\n\n" +
        "`skill_id` must match /^[a-z][a-z0-9_]{0,63}$/ and may NOT collide with a factory skill.",
      parameters: Type.Object({
        skill_id: Type.String({
          description: "Unique identifier, lowercase alphanumeric + underscore (e.g. welcome_home).",
        }),
        label: Type.Optional(
          Type.String({ description: "Chinese display name shown in the Web UI (e.g. '欢迎回家')." }),
        ),
        description: Type.String({
          description: "One-line summary — surfaced to the fast-path LLM agent as the skill's tool description.",
        }),
        parameters: Type.Optional(
          Type.Record(
            Type.String(),
            Type.Object({
              type: Type.String({ description: "One of: str | int | float | bool." }),
              description: Type.Optional(Type.String()),
              required: Type.Optional(Type.Boolean()),
              default: Type.Optional(Type.Any()),
            }),
            { description: "Outer parameters exposed to callers (optional)." },
          ),
        ),
        steps: Type.Array(
          Type.Object({
            // Both keys are optional in the TypeBox schema — the server-side
            // validator enforces "exactly one of skill_id / trajectory" and
            // returns a structured error if both or neither are present.
            skill_id: Type.Optional(
              Type.String({
                description:
                  "Factory skill to invoke (nod, move_to, set_expression, …).  Mutually exclusive with `trajectory`.",
              }),
            ),
            params: Type.Optional(
              Type.Record(Type.String(), Type.Any(), {
                description:
                  "Params for the factory skill; strings may use {placeholder} to reference outer params.",
              }),
            ),
            trajectory: Type.Optional(
              Type.Object(
                {
                  waypoints: Type.Array(
                    Type.Object({
                      joints: Type.Record(Type.String(), Type.Number(), {
                        description:
                          "Target joint angles in degrees.  Unspecified joints hold their previous value " +
                          "(for the first waypoint: the robot's current live pose).",
                      }),
                      duration: Type.Optional(
                        Type.Number({
                          description:
                            "Seconds to travel from the PREVIOUS waypoint to this one.  " +
                            "Ignored on the first waypoint (it's the starting pose).  ≥ 0.",
                        }),
                      ),
                    }),
                    {
                      description:
                        "Ordered keyframes (≥ 2, ≤ 50).  First is the starting pose; each subsequent one " +
                        "is a target the robot eases into over `duration` seconds.",
                    },
                  ),
                  fps: Type.Optional(
                    Type.Integer({
                      description:
                        "Frame streaming rate in Hz.  Default 50; valid range [10, 100].  " +
                        "Higher = smoother but more bus traffic.",
                    }),
                  ),
                  interpolation: Type.Optional(
                    Type.String({
                      description:
                        "Easing function between waypoints.  One of: linear | ease_in_out_cubic (default) | " +
                        "ease_in_out_quad | ease_out_cubic | ease_in_cubic | ease_out_back.",
                    }),
                  ),
                  ease_overshoot: Type.Optional(
                    Type.Number({
                      description:
                        "Overshoot factor for ease_out_back ∈ [0.0, 0.5], default 0.10.  Ignored for other eases.",
                    }),
                  ),
                },
                {
                  description:
                    "Custom joint-trajectory step.  Use only when no factory skill matches the motion shape. " +
                    "Mutually exclusive with `skill_id`.",
                },
              ),
            ),
          }),
          { description: "Ordered step list — executed sequentially, aborts on first error." },
        ),
        overwrite: Type.Optional(
          Type.Boolean({ description: "Default true; set false to fail fast when skill_id already exists." }),
        ),
      }),
      async execute(_toolCallId, params) {
        const base = getBase();
        const definition: Record<string, unknown> = {
          skill_id: params.skill_id,
          description: params.description,
          steps: params.steps,
        };
        if (params.label !== undefined) definition.label = params.label;
        if (params.parameters !== undefined) definition.parameters = params.parameters;

        const result = await postJson<{
          ok: boolean;
          result?: { skill_id: string; path: string; updated: boolean; reason?: string };
          error?: string;
        }>(`${base}/api/skills/save`, {
          definition,
          overwrite: params.overwrite ?? true,
        });
        if (!result.ok) {
          throw new Error(result.error || "save skill failed");
        }
        const r = result.result!;
        return toolOk(
          `${r.updated ? "Updated" : "Created"} skill '${r.skill_id}' at ${r.path}.\n` +
            `It is now registered and callable via lampgo's standard invoke path.`,
        );
      },
    });

    api.registerTool({
      name: "lampgo_delete_skill",
      label: "Delete lampgo composed skill",
      description:
        "Remove a user-authored composed skill.  Factory skills cannot be deleted (the server rejects the call). " +
        "Use this when the user explicitly asks to drop a skill they previously had you create.",
      parameters: Type.Object({
        skill_id: Type.String({ description: "Identifier of the user skill to remove." }),
      }),
      async execute(_toolCallId, params) {
        const base = getBase();
        const result = await postJson<{
          ok: boolean;
          result?: { skill_id: string; file_removed: boolean; reason?: string };
          error?: string;
        }>(`${base}/api/skills/delete`, { skill_id: params.skill_id });
        if (!result.ok) throw new Error(result.error || "delete skill failed");
        const r = result.result!;
        return toolOk(
          `Deleted skill '${r.skill_id}'${r.file_removed ? " (file removed)" : " (file was already missing)"}.`,
        );
      },
    });

    api.registerTool({
      name: "lampgo_list_skills",
      label: "List lampgo skills",
      description:
        "Return the full lampgo skill registry with provenance (`source: factory | user`), description, " +
        "and parameter schema for each skill.  Call this *before* `lampgo_save_skill` so you know which " +
        "factory skills are available as step primitives — their `skill_id` values are the only values " +
        "allowed inside `steps[*].skill_id`.",
      parameters: Type.Object({}),
      async execute() {
        const base = getBase();
        const data = await getJson<unknown>(`${base}/api/skills`);
        return toolOk(JSON.stringify(data, null, 2));
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
