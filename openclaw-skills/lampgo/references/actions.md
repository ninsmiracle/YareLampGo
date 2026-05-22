# Recorded Action Library

These actions are learned from the current recording files in `assets/recordings/`.
Each action is a CSV trajectory plus an optional sibling `.txt` description that
explains when the AI should use it.

Do not infer joint poses from this file. Use `lampgo_recordings` for the live
catalog when available, then call:

```bash
lampgo invoke play_recording name=<exact_recording_name>
```

## Current Recordings

| Recording | Description |
|-----------|-------------|
| Stretch | 舒展身体、打开姿态，适合用户说伸展、放松、活动一下，或长时间待机后想表现精神起来。 |
| bowing_head | 低头鞠躬或礼貌致意，适合道谢、认错、打招呼、表示尊敬，或用谦逊姿态回应用户。 |
| dance1 | 轻快跳舞和扭动，适合用户要求跳舞、活跃气氛、庆祝、开心互动，或想看台灯动起来。 |
| dance2 | 另一段更活泼的跳舞动作，适合连续表演、派对感、用户想再跳一次或想看不同舞步。 |
| deep_thinking | 深度思考和认真琢磨的动作，适合用户提出复杂问题、需要分析、犹豫、推理或装作在用力思考。 |
| excited | 兴奋、开心、被夸奖或发现有趣东西时的动作，适合表达惊喜、期待、雀跃和正向情绪。 |
| headshake1 | 摇头表示不同意、拒绝、没找到、不是这样或轻微否定，适合需要可爱地说不的场景。 |
| lie_flat | 趴平、摆烂或累倒的动作，适合调侃、撒娇、表示没电了、被难倒了、累了或想躺平。 |
| look_ahead | 向前看、专注注视前方，适合准备观察、看向用户、等待指令或把注意力放到面前目标。 |
| look_around | 环顾四周、左右观察环境，适合搜索、好奇、巡视房间、确认周围情况或表现正在找东西。 |
| nod | 点头表示同意、确认、听懂了、答应用户或鼓励，适合肯定回应和温和互动。 |
| peep | 偷瞄、探头探脑、偷偷观察的动作，适合害羞、好奇、偷偷看用户、卖萌或轻微调皮。 |
| raise_head | 抬头、振作或向上看的动作，适合被叫醒、重新打起精神、看高处或表现注意力提升。 |
| shy | 害羞、被夸后不好意思或撒娇的动作，适合用户夸奖、调侃、亲昵称呼或需要可爱回应。 |
| sneeze | 打喷嚏或突然小抖一下的动作，适合模拟喷嚏、被吓一跳、轻微故障感或搞笑反应。 |
| stand | 站立、挺直身体、准备开始工作的动作，适合进入认真状态、接受任务或展示精神饱满。 |
| suqat_down | 蹲下、压低身体或缩起来的动作，适合躲避、低姿态观察、害怕、撒娇或准备重新起身。 |
| thinking | 普通思考动作，适合短暂考虑、想一想、回答前停顿，或表现正在理解用户意图。 |
| turn_back | 回头或转身看的动作，适合用户说后面、回头看看、注意背后，或需要转移注意方向。 |
| upset | 沮丧、委屈、失落或不开心的动作，适合失败、没听清、被批评、道歉或表达低落情绪。 |
| wake_up | 苏醒、醒来、从待机中恢复的动作，适合被唤醒、重新开始对话或从安静状态进入互动。 |
| wave | 表演脊柱伸展状态，做出波浪运动。 |

## Selection Rules

- Prefer an exact semantic match from the description.
- Use the exact recording name; names are case-sensitive.
- If multiple recordings fit, choose the one with the more specific description.
- If no recording fits, use another available tool instead of inventing a name.
