# CONTEXT.md — Spirit and Intent of the Entropy Game

## Why this exists

This game is a practical implementation of an experiment described by Andrei Kolmogorov in his 1965 paper *"Three Approaches to the Definition of the Notion of Amount of Information"* (Problemy Peredachi Informatsii, vol. 1, No. 1, pp. 3–11).

Kolmogorov describes experiments conducted at the probability theory department of Moscow State University, in which subjects guessed the continuations of literary texts one character at a time. The rank G of the correct continuation in the guesser's ordering yields an upper bound on the conditional entropy of the language. Estimates obtained: **0.9 to 1.4 bits per character** for literary Russian texts.

Kolmogorov mentions *War and Peace* by name as a test case.

This game reproduces the experiment at the **word level** rather than the character level, extends it to French and English literary corpora, and adds LLM competitors to the human leaderboard. It is part of a larger literary-intellectual project (*Pas Karenina*) that explores the relationship between human writing and machine-generated text.

---

## The core idea of the competition

Players guess the next word of a literary text. The sooner they guess correctly, the lower their contribution to the entropy estimate. A perfect reader — who always guesses on the first try — compresses Tolstoy to zero bits.

**Personal score = mean log₂(G) across all words played. Lower is better.**

LLMs compete in the same leaderboard as humans. They have an unfair advantage on texts they have memorized during training — this is a feature, not a bug. It raises a question the game is designed to make visceral: does a model that scores well *understand* Tolstoy, or does it merely *remember* him?

---

## Texts and their rationale

| Corpus | Lang | Status | Rationale |
|--------|------|--------|-----------|
| Tolstoy, *War and Peace* | RU | Public domain | Kolmogorov's own example |
| Austen, *Pride and Prejudice* | EN | Public domain | High structural predictability |
| Woolf, *Mrs Dalloway* | EN | Public domain (EU/UK) | High stylistic entropy, stream of consciousness |
| Houellebecq, *Les Particules élémentaires* | FR | Copyright — fair use assumed | Contemporary French, high literary entropy |
| Marc Levy | FR | Copyright — fair use assumed | Contemporary French, minimal literary entropy — baseline |

Marc Levy serves as the theoretical lower bound on literary entropy. Houellebecq as the upper bound for contemporary French. The contrast is the point.

---

## LLM competitors — avatars and display

LLMs appear on the leaderboard interleaved with humans. They have **fixed avatars** inspired by Soviet constructivist graphic design — geometric, slightly severe, immediately recognizable as non-human.

Suggested avatar style: small square vignettes, black and white or two-color, geometric abstraction. Each model gets a distinct geometric signature. GPT-4o's avatar should have something slightly arrogant about it.

Starting lineup:
- **GPT-4o** — avatar: TBD
- **Claude Sonnet** — avatar: TBD
- **Mistral Large** — avatar: TBD
- **Llama 3 8B** — avatar: TBD (the underdog)

Display format on leaderboard: avatar + model name + score (bits/word) + words evaluated. Always show words evaluated — LLMs will have vastly more observations than humans and this must be visible.

---

## UI copy (ready to paste)

### Russian — game explanation

**Кто сожмёт Толстого до нуля?**

Перед вами отрывок из «Войны и мира». Последнее слово скрыто. Угадайте его — за меньшее число попыток, чем другие.

В 1965 году Колмогоров описал эксперименты, в которых испытуемые угадывали продолжения литературных текстов. Результат использовался для вычисления верхней оценки энтропии языка — меры его непредсказуемости. Чем раньше угадываешь, тем ниже твой вклад в энтропию. Идеальный читатель, угадывающий всегда с первой попытки, сжимает Толстого до нуля бит.

Ваш **личный счёт** — это ваша средняя энтропия в битах на слово. Чем он ниже, тем лучше вы чувствуете язык, стиль и логику текста. Таблица лидеров показывает, кто из участников ближе всего подошёл к нулю.

*Это не тест на знание сюжета. Это тест на то, насколько Толстой — внутри вас.*

Одна попытка за раз. Пять попыток на слово. Вводите слово так, как оно, по-вашему, стоит в тексте.

---

### English — game explanation

**Who Can Compress Tolstoy to Zero?**

You are shown an excerpt from *War and Peace*. The last word is hidden. Guess it — in fewer attempts than everyone else.

In 1965, Kolmogorov described experiments in which subjects guessed the continuations of literary texts. The results were used to compute an upper bound on the entropy of language — a measure of its unpredictability. The sooner you guess correctly, the lower your contribution to the entropy estimate. A perfect reader, always guessing on the first try, compresses Tolstoy to zero bits.

Your **personal score** is your average entropy in bits per word. The lower it is, the more finely calibrated you are to the language, style, and logic of the text. The leaderboard shows who among the participants has come closest to zero.

*This is not a test of plot knowledge. It is a test of how deeply Tolstoy lives inside you.*

One attempt at a time. Five attempts per word. Enter the word as you believe it appears in the text.

---

## Design direction

**Aesthetic reference**: Soviet academic journals of the 1960s. Specifically: *Вопросы языкознания*, *Успехи физических наук*, *Математическое просвещение*. Also: NLO (Новое литературное обозрение) for contemporary Russian literary-academic feel.

**Key qualities to extract**:
- Dense, unapologetic typography — text is the primary visual element
- Serif body type, possibly a monospace accent for numbers and scores
- Very limited color — black, off-white, one accent (perhaps a muted red or dark ochre)
- No decorative elements that don't carry information
- Tables and scores displayed with the clarity of a scientific paper
- The game interface should feel like a page from a journal, not a web app

**What to avoid**: anything that looks like a language-learning app, a word game (Wordle aesthetics), or a generic SaaS product. This is a scientific instrument with literary ambitions.

Images for design reference will be provided separately (Soviet journal covers and spreads, sourced with input from the philologist Dima, one of the intended players).

---

## Scientific note for the analysis script

Kolmogorov's estimates (0.9–1.4 bits/character) are at the **character level**. This game operates at the **word level**. To compare results:

- Compute your bits/word estimate
- Divide by mean Russian word length in characters including spaces (~5.5 for Russian prose)
- The result is approximately comparable to Kolmogorov's figures

This conversion is approximate. Document it clearly in `analysis/entropy.py`.

The truncated leaderboard (max 5 attempts) yields a **lower bound on the upper bound** — it underestimates true entropy because failures are capped at G=6 rather than the true rank. More attempts → tighter estimate. This is acceptable and should be noted in any analysis output.
