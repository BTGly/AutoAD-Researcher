# Release integrity record

The binary ZIP stored in commit `f657a08e5286e384532ac5643e55b81c2765aade` was truncated and must not be used.

The corrupted ZIP has been removed. The executable ZIP is now reconstructed from Git-safe text segments by `rebuild_release_zip.py`.

## Expected reconstructed artifact

- File: `AutoAD_MVTec_MPDD_4090x2_UAT_2026-07-25.zip`
- Size: `34,648` bytes
- SHA256: `95a3b970b5f29d52026aba178e3cca9ae667159e8e520a650db22349cb239077`
- ZIP members: `43`
- Central directory: valid
- Member CRC test: pass

## Verified Git blob manifest

```text
part00   76a8e8671eb610dd7374c369ad5d23ebf257ba98
part01   c0d5ef1b2e4305e33d6ab0d2063cedd61a28e483
part02   cbc076f0df3f64fac03f1d9b642afbc4ffa4e267
part030  6aad13f4d3b4a54b06d1b7cfd3c6a14cc8f24cd3
part031  fc756efb78197cce2f9fa7a30db3f8d1917bb54e
part032  a8c750fa337a897067edf35e43d5d28f2e986d73
part033  ed705942dd58f7a4627d832dcb29f62ab255a821
part034  de9bf3de7f2e1531aff4be5dabc7b3550c4d188e
part035  d519dded90509dda61e608532dacea6be1848c52
part04   5904852d87b06fe87cbf30d8b4edc08b7780d91a
part05   9aab14d5760c40f2aaafe7a58f3b1742905dfa2e
part06   f467ff6efe33d196db1c3067d0d6d4d2c702f95a
part07   d856145831956a595abef011b534ee83c1c6d43d
```

Each SHA above was compared with the corresponding local source segment before the reconstruction test.

## Rebuild and verify

```bash
cd 'notes/uat/AutoAD_双4090_MVTec_MPDD_真实训练验收包_2026-07-25'
python3 rebuild_release_zip.py
sha256sum -c AutoAD_MVTec_MPDD_4090x2_UAT_2026-07-25.zip.sha256
unzip -t AutoAD_MVTec_MPDD_4090x2_UAT_2026-07-25.zip
```

`rebuild_release_zip.py` refuses to emit the ZIP if the segment names, encoded length, decoded length, SHA256, central directory, or any member CRC is incorrect.
