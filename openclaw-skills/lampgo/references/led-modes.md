# LED Expression Keys (34 modes)

Use the exact `Name` column in OpenClaw tool calls.

| Mode # | Name | 中文含义 | Description |
|--------|------|----------|-------------|
| 0 | off | 熄灭 | All LEDs off |
| 1 | red | 红色逐圈 | Red ring fill |
| 2 | green | 绿色逐圈 | Green ring fill |
| 3 | blue | 蓝色逐圈 | Blue ring fill |
| 4 | white | 白色逐圈 | White ring fill / desk light |
| 5 | theater | 剧场追逐 | Theater chase (white) |
| 6 | theaterred | 红色剧场 | Theater chase (red) |
| 7 | theatergreen | 绿色剧场 | Theater chase (green) |
| 8 | theaterblue | 蓝色剧场 | Theater chase (blue) |
| 9 | rainbow | 彩虹渐变 | Rainbow gradient |
| 10 | rainbowchase | 彩虹追逐 | Rainbow theater chase |
| 11 | left | 左箭头 | Left arrow |
| 12 | right | 右箭头 | Right arrow |
| 13 | up | 上箭头 | Up arrow |
| 14 | down | 下箭头 | Down arrow |
| 15 | check | 对号 | Check mark |
| 16 | cross | 叉号 | Cross / X mark |
| 17 | exclaim | 感叹号 | Exclamation mark |
| 18 | question | 问号 | Question mark |
| 19 | star | 星星 | Star |
| 20 | music | 音符跳动 | Jumping music note |
| 21 | smiley | 开心 | Happy face |
| 22 | sad | 伤心 | Sad face |
| 23 | heart | 心动 | Pulsing heart |
| 24 | surprised | 惊讶 | Surprised face |
| 25 | blush | 害羞 | Blushing face |
| 26 | angry | 生气 | Angry face |
| 27 | thinking | 思考 | Thinking animation |
| 28 | sleep | 睡觉 | Sleep / Zzz animation |
| 29 | helpless | 无奈 | Helpless / sweat animation |
| 30 | cool | 耍酷 | Cool face |
| 31 | focused | 专注 | Focused face |
| 32 | wink | 眨眼 | One-eye wink |
| 33 | myu7gt | YU7 GT | Left panel white YU7, right panel red GT |

Aliases: `myu7`, `mgt`, `yu7gt`, and `yu7` resolve to `myu7gt`.

## Invocation

```bash
lampgo invoke set_expression mode=smiley
lampgo invoke set_expression mode=focused
lampgo invoke set_expression mode=wink
lampgo invoke set_expression mode=myu7gt
```
