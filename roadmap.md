# Roadmap

- [ ] Gradients and shadows in the spec
- [ ] Tighter Apple and Netflix examples (diagonal overshoots the bars)
- [ ] architecture.svg, SECURITY.md per repo standard
- [ ] Paint the Last Supper at 4000 layers, add its GIF to the README and landing
- [ ] Polish the Mona Lisa: she reads 8-bit at 3000 squares. Try the brushstroke mode and a higher budget once paint is faster
- [ ] bcgd logo: paint turns small text to mush and transparency to black. Rebuild it as a real logo spec with text layers and arc_text
- [ ] Paint speed: build inside group layers of about 200 so inserts stay cheap as the document fills. Measure first
- [ ] Paint speed: probe whether fill color can go inside `make` (one round-trip per layer instead of two)
- [ ] Paint quality: merge neighbor cells of the same color into one wide rectangle
- [ ] Paint quality: rotated ellipses fitted by error, not a grid, for a brushstroke look
