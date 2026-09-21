# Roadmap

- [ ] Landing page live with in-browser painter demo (done, polish pending)
- [ ] architecture.svg, SECURITY.md per repo standard
- [ ] Gradients and shadows in the spec
- [ ] Tighter Apple and Netflix examples (diagonal overshoots the bars)
- [ ] Polish the Mona Lisa: recognizable at 2997 layers in eight minutes, but 8-bit. Try brushstroke mode once paint is faster
- [ ] Paint the Last Supper at 4000 layers, add its GIF to the README and landing (parked)
- [ ] bcgd garage door logo: rebuild as real logo spec with text layers and arc_text instead of painted rectangles (currently 94 seconds but mushy text)
- [ ] Paint speed: build inside group layers of about 200 so inserts stay cheap as the document fills. Measure first
- [ ] Paint quality: merge neighbor cells of the same color into one wide rectangle
- [ ] Paint quality: rotated ellipses fitted by error, not a grid, for a brushstroke look
