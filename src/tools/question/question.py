def question(questions: list[dict]) -> str:
    answers = {}

    for q in questions:
        text = q["question"]
        options = q.get("options", [])

        print(f"\n{text}")

        if options:
            for i, opt in enumerate(options, 1):
                label = opt["label"]
                desc = opt.get("description", "")
                suffix = f" — {desc}" if desc else ""
                print(f"  {i}. {label}{suffix}")
            raw = input("Choice (number or label): ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                answer = options[int(raw) - 1]["label"]
            else:
                answer = raw
        else:
            answer = input("Answer: ").strip()

        answers[text] = answer

    return ", ".join(f'"{k}"="{v}"' for k, v in answers.items())
