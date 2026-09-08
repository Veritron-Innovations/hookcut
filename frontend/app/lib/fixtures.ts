/**
 * Sample data for developing the frontend without a running backend.
 *
 * Deliberately awkward in places - one very long angle name, one very short
 * source line, captions that run past two lines - so layouts get designed
 * against realistic worst cases rather than tidy placeholder text.
 */

import type { Concept } from "./types";

export const SAMPLE_CONCEPTS: Concept[] = [
  {
    index: 0,
    angle_name: "The chorus",
    start_timestamp: "01:12",
    end_timestamp: "01:34",
    source_text:
      "And I keep the porch light on for nobody, keep the porch light on for nobody at all",
    text_overlay_options: [
      "when you're still waiting on someone who left",
      "the most honest line I've ever written",
      "nobody talks about this part of moving on",
    ],
    tiktok_caption: "who else keeps the porch light on 😭 new one out friday",
    ig_caption:
      "keep the porch light on for nobody at all 🕯️\n\n#afrornb #newmusic #songwriter #indieartist #rnbsoul",
  },
  {
    index: 1,
    angle_name: "The confession in the second verse that nobody expects",
    start_timestamp: "02:03",
    end_timestamp: "02:21",
    source_text:
      "I told my mother I was fine on the phone last night and then I sat in the car for an hour",
    text_overlay_options: [
      "we've all had this phone call",
      "the thing I said vs the thing I meant",
      "lying to your mum is a love language actually",
    ],
    tiktok_caption: "the car sit after the phone call is universal, right? 🚗",
    ig_caption:
      "told her I was fine. sat in the car for an hour.\n\n#songwriting #newmusic #afrobeats #vulnerable #rnb",
  },
  {
    index: 2,
    angle_name: "Tonal shift",
    start_timestamp: "00:41",
    end_timestamp: "00:58",
    source_text: "But the city don't stop for grief",
    text_overlay_options: [
      "the city doesn't care that you're heartbroken",
      "one line that changed the whole song",
      "grief doesn't get a day off and neither do you",
    ],
    tiktok_caption: "this line took me 3 months to write and 4 seconds to sing",
    ig_caption: "the city don't stop for grief 🌃\n\n#lyrics #rnb #newmusic #afrornb",
  },
  {
    index: 3,
    angle_name: "Opening hook",
    start_timestamp: "00:08",
    end_timestamp: "00:29",
    source_text:
      "There's a version of me that never left Nairobi and I think about him more than I should",
    text_overlay_options: [
      "the version of you that never left",
      "diaspora kids will understand this one",
      "leaving home was the right call and I still grieve it",
    ],
    tiktok_caption:
      "there's a version of me that never left and honestly he seems happier 🫠 out friday",
    ig_caption:
      "a version of me that never left 🇰🇪\n\n#nairobi #diaspora #afrornb #newmusic #songwriter",
  },
  {
    index: 4,
    angle_name: "The bridge",
    start_timestamp: "02:47",
    end_timestamp: "03:09",
    source_text: "So I'm learning how to be a stranger to somebody I know by heart",
    text_overlay_options: [
      "learning to be a stranger to someone you know by heart",
      "the hardest part of the breakup nobody warns you about",
      "you don't get over people, you just get further away",
    ],
    tiktok_caption: "a stranger to somebody I know by heart 💔 which line hits hardest?",
    ig_caption:
      "learning how to be a stranger to somebody I know by heart.\n\n#rnb #breakupsongs #newmusic #songwriter #afrornb",
  },
];

/** Concepts as they come back after rendering - same data plus a clip url. */
export const SAMPLE_RESULTS: Concept[] = SAMPLE_CONCEPTS.slice(0, 3).map((c) => ({
  ...c,
  // No real file exists in mock mode; the card renders a placeholder instead.
  clip_url: null,
}));
