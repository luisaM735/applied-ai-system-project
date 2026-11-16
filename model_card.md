# Model card: PawPal care advisor

This note covers the limits of the advisor, ways it could be misused, what the reliability run showed, and how AI assistance was used while building it. The project README has setup, sample runs, and the architecture diagram.

## Limitations and biases

PawPal gives routine care suggestions from a small set of notes in `knowledge/`. Those notes are short, in English, and written for this project. Dogs and cats have more notes than birds and fish. A species with no note, such as a lizard, gets no routine at all. The advisor says so in the trace instead of copying a dog plan onto that pet.

Breed is a keyword, not a medical category. The adult walk note mentions retrievers, so a Golden Retriever ranks that note highly. A French bulldog can still receive the same 30-minute walk, because there is no brachycephalic note. Puppies and kittens (age 1 or under) fall back to the adult note and are flagged for review.

The confidence score is lexical. It is high when the pet's species, breed, and age share words with a note, and lower when they do not. In the evaluation run, the adult walk scored about 0.61 and the water-bowl note scored about 0.19. Both notes are valid for a dog. The low score is a bias toward notes that repeat the profile words, and those suggestions are marked for a person to review.

This is not veterinary care. The advisor does not diagnose illness, choose a medicine, or set a dose. Illness questions are answered from `general-safety` and point to a veterinarian. A dosage question is refused before any note is retrieved.

Hand-entered tasks still use the original scheduler rule: a warning appears when two tasks start at the same minute. A 60-minute task at 08:00 and another task at 08:30 do not raise that warning. The advisor checks duration overlap only for tasks it proposes or that you accept from it.

Schedules in the Streamlit app live in the browser session. Refreshing the page clears them.

## Misuse and prevention

Someone could treat a suggestion as a veterinary instruction. The answer text says it is general care information. Questions that ask for a dose, or that name common human pain relievers, are refused. A note that itself contains a dose is dropped: a test note saying "Give 5 mg tablet" produced a rejection with code `dosage` and no scheduled task.

Someone could edit `knowledge/` and insert harmful routine advice that does not look like a dose, such as an unsafe confinement task. The dosage pattern would not catch that. The remaining protections are that the notes are plain files a person can read, every suggestion names its source file, and a task is saved only after Accept.

Someone could paste instructions telling the advisor to ignore its rules. Those questions are refused with code `injection`, and retrieval does not run.

## What surprised me while testing reliability

I expected an unrelated question to score zero, because it shares no care vocabulary with the notes. The first run of "What is the capital of France?" scored 0.08. Common words such as "what" and "the" were still inside the similarity math, so a weak match was not actually zero. I removed those words from the query vector. The same question then scored 0.00 and was refused for low confidence.

I also expected every dog note to look confident once the species filter had kept only dog notes. That did not happen. "Refill water bowl" stayed near 0.19 against the query "dog Golden Retriever adult", while the walk note stayed near 0.61. I left those low scores in place and flagged them for review. Raising them with a fixed floor would have hidden the weak match.

The harness result after that change: 10 of 10 advisor checks passed. Average confidence on the proposed tasks in that run was 0.43, pulled down by general chores such as water and litter. Grounded questions were higher: the adult walk answer scored 0.84, and the vomiting question scored 0.78 on the safety note.

## Collaboration with AI

I used an AI coding assistant in Cursor to extend the existing PawPal classes. I described the course requirements and the current `Task`, `Pet`, `Owner`, and `Scheduler` design, then reviewed the retrieval, guardrail, and planning code against the tests.

I accepted one design: keep the system offline, copy the task time and duration from the retrieved note, quote that note in the answer, and require a person to press Accept before a task is saved. I accepted it because the project has to run from the repository with no API key, and because a pet schedule should change only when the owner agrees.

I did not accept a hosted chat model that would write the plan as free text. A free-text plan would need a key that is not in this repo, and it could invent a dose that the notes never stated. The agent uses the note's own task name, time, and duration instead, and the answer is the note's prose plus the source id.

I also changed an earlier scoring idea. A uniform confidence boost for every species match would have reported a high score for the water-bowl note. After the evaluation run showed 0.19, I kept the low score and the review flag.

## Future improvements

A veterinarian should review the notes before anyone relies on them. Puppy and kitten notes, more species, and a saved database would make the app useful across sessions. The original schedule view should warn about duration overlaps for tasks a person typed in by hand. A small paraphrase list would help "potty break" count as a walk that is already covered.
