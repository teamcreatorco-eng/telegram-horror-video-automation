# Speech-first scene sound balance

User reports rain-like noise masks the voice in the actual uploaded radio-story export.
Inspection of the automatic sound report found zero rain cues but ten radio-static cues
lasting 35.3 seconds in total. Radio had been treated as whole-shot ambience at excessive
noise level and was mixed without speech ducking. The source audio is already mixed;
no clean reference stems are available. Mixed 5-second RMS measurements of Web 6 vary
approximately -25 to -14 dB, which does not isolate its music or individual effects.

Preserve original uploaded audio timing and video packets. Reuse the automatic DeepSeek
plan, without new model calls. Bound radio textures to 0.55 seconds at reduced RMS and
sizzling to 1.1 seconds, including synthesized fallbacks. Separate sustained fire/rain/wind
ambience from action effects and music. Keep sustained ambience around -38 dB RMS before
ducking and duck it further from speech; leave brief impacts on their separate bus.
Do not add rain, fire or other sound sources absent from the supplied plan. These changes
do not claim exact reference replication or automatic recognition of actual motion onsets.

The revised exported radio film uses the original 20 planned cues but only 5.5 seconds
of total radio static. No rain cues. Full decode and unchanged-video-packet hash checks
pass. No GPU was resumed. The original source file is retained, and the new film is saved
under Radio-Story-Balanced-Scene-Sound.mp4. Regression tests cover short textures,
synthesized fallback, quiet/separate ambient bus, and valid FFmpeg mixing with sidechain.
