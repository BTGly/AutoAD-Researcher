# Release integrity record

The binary ZIP stored in commit `f657a08e5286e384532ac5643e55b81c2765aade` was truncated and must not be used.

The corrupted ZIP has been removed. The executable ZIP is now reconstructed from Git-safe text segments by `rebuild_release_zip.py`.

## Expected reconstructed artifact

- File: `AutoAD_MVTec_MPDD_4090x2_UAT_2026-07-25.zip`
- Size: `40,181` bytes
- SHA256: `11a5a9848df40532387324c501e39b97eb0add38b7f0b9e9efbe66c6d6333650`
- ZIP members: `46`
- Central directory: valid
- Member CRC test: pass

## Verified Git blob manifest

```text
part00   5a2e342f0d002a470ce7df1ecbd23406da4d0e7a
part01   d1ea2412d97a5d2a3afa72857b427aecf1919d1b
part02   a2b3382b3baf487d406ef8be4e0eacd8ae226a44
part030  2cb8e35bd371b15b487e8dff3737bd89f71b004d
part031  b49c840ba9440aa98f640adae443813e9b71c998
part032  65a2bdf67045c85819fef0a6f01d5795471914b8
part033  0c1ce962bf399665649f1dbb84d3c5329475fad1
part034  035c5ca159766443dd04232dd7362d2f9baa0947
part035  1a08c6aea7a5a6555e7e7729b2180e040d7e86e5
part04   2c1e639be2e25a1694a22bec54aa664ccb246cfa
part05   d2e1066adc23f0a136c7c216f966f31508cb1d72
part06   a8f16e07e465533df79e3988333e09ab2c46fe83
part07   f4df102f750744f1fda71c5f16a488e57b01f531
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
