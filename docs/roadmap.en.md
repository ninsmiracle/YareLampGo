# Roadmap

This is not a promised schedule. It is a public draft of directions we want to try, and places where outside contributors can help.

## Replaceable Head Modules

YareLampGo starts as a lamp, but we do not want the head to stay locked to only one shape. A better future version could use magnetic or snap-on modules, so the head is easy to swap and easier for the community to modify.

Possible directions:

- Lamp head: lighting, fill light, and expressive LEDs.
- Phone holder: a moving desktop filming stand.
- Small speaker: voice feedback and motion in one desktop device.
- Airflow module: this needs extra power, thermal, and safety design before it should be treated as practical.

## Algorithm Extensions

The repo does not ship VLA, RL, or a large training stack today. The first goal is to make the hardware, motion runtime, safety boundary, Web control, and Agent interface usable.

That said, algorithm contributions are welcome:

- Data collection and motion datasets.
- Imitation learning, policy learning, and RL.
- VLA / vision-language-action models.
- Better perception, tracking, and desktop scene understanding.

The best starting point is to plug into the existing motion system, skill layer, Web API, or OpenClaw interface instead of replacing the whole stack.

## Desktop Scenarios

We would love to see more everyday use cases:

- Desktop filming assistant.
- Reaction prop for livestreams or videos.
- Motion driven by music or voice mood.
- Agent-controlled hardware with user confirmation.
- User-made motions and skill scenes.

The project should stay easy to run, easy to remix, and easy to reproduce. Serious work can grow from there.
