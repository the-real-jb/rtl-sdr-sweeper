# RTL-SDR sweeper: additional VHF/UHF signals & demodulation reference

Research date: 2026-09-26. Scope: hobby monitoring with R820T (~24–1766 MHz), southeastern US (coastal SC/GA/NC, inland cities). **Excluded from Section 1** because the backend already watches them: 121.500, 144.390, 145.800, 146.520, 156.800, 162.400–162.550, 433.920, 446.000 (labeled PMR446 in config; in the US this frequency is primarily **70 cm FM simplex calling**, not European PMR446), 1090 MHz ADS-B, and Palmetto 800 P25. Encrypted traffic and key recovery are out of scope.

---

## 1. Additional high-value signals (not in the current watch list)

Listen mode key: **NFM** = narrow FM voice; **WFM** = broadcast FM; **AM** = amplitude modulation voice; **Decoder** = needs a dedicated app (IQ or discriminator/audio pipe), not intelligible speech from analog modes alone.

| Service | Center freq (MHz) | Typical RF BW | Listen / decode | SE US relevance | Source |
|--------|-------------------|---------------|-----------------|-----------------|--------|
| **UAT ADS-B** | 978.000 | ~1–2 MHz capture at ~2.08 MS/s IQ | **Decoder** (`dump978-fa` / UAT tools; not WFM/NFM) | US-only adjunct to 1090 ES; common near airports and GA traffic | https://github.com/wiedehopf/dump978 |
| **AIS 1 / AIS 2** | 161.975 / 162.025 | 25 kHz each (GMSK/FM, 9600 bit/s) | **Decoder** (`rtl_ais`, `AIS-catcher`, `gr-ais`; NFM alone shows bursts only) | Strong on Atlantic/Gulf/Intracoastal Waterway, ports (Charleston, Savannah, Jacksonville) | https://www.itu.int/dms_pubrec/itu-r/rec/m/R-REC-M.1371-6-202602-I!!PDF-E.pdf |
| **Marine DSC** | 156.525 (Ch 70) | 25 kHz marine channel | **Decoder** (DSC apps / `multimon-ng` FSK family; voice not allowed on Ch 70) | Distress/calling; announces voice moves to working channels | https://navcen.uscg.gov/us-vhf-channel-information |
| **USCG marine weather (voice)** | 157.100 (Ch 1022, formerly 22A) | 25 kHz | **NFM** | Scheduled wx/safety broadcasts (announced on Ch 16); coastal SE | https://www.navcen.uscg.gov/marine-safety-information-broadcasts |
| **Marine working voice** | e.g. 156.650 (Ch 22), 156.600 (Ch 12), 157.850 (Ch 85) | 25 kHz | **NFM** | Intership, port ops, bridges/locks; scan Ch 9/12/13/22/68–72 as local activity dictates | https://navcen.uscg.gov/us-vhf-channel-information |
| **GMRS/FRS simplex & repeaters** | 462.5625–462.7250; 467.5625–467.7125; GMRS repeater **inputs** 467.5500–467.7250 (25 kHz steps) | 12.5 kHz (FRS / interstitial); up to 20 kHz (GMRS main) | **NFM** (often 12.5 kHz filter; GMRS may sound wide) | Suburban/rural consumer radio; repeaters on 462.55–462.725 | https://www.ecfr.gov/current/title-47/chapter-I/subchapter-D/part-95/subpart-B ; https://wiki.radioreference.com/index.php/FRS/GMRS_combined_channel_chart |
| **MURS** | 151.820, 151.880, 151.940, 154.570, 154.600 | 11.25 or 20 kHz | **NFM** | Business/farm/church short-range; no license | https://www.fcc.gov/wireless/bureau-divisions/mobility-division/multi-use-radio-service-murs |
| **POCSAG / FLEX paging** | **929–930** & **931–932** (12.5 kHz steps, e.g. 931.9375); also **152–158**, **454–462**, **467.750–467.925** | 12.5–25 kHz | **Decoder** (`multimon-ng` + `rtl_fm` @ 22050 Hz raw; or dedicated pager apps) | Hospital/fire/legacy paging; 900 MHz needs stable PPM on RTL-SDR | https://www.fcc.gov/wireless/bureau-divisions/mobility-division/paging ; https://en.wikipedia.org/wiki/POCSAG |
| **ACARS (plain old ACARS)** | **131.550** (world primary); also **130.025**, **130.425**, **131.125**, **129.125** (US/common) | ~5 kHz MSK on AM carrier | **Decoder** (`acarsdec`, `dumpvdl2`/ACARS modes); **AM** filter helps IQ, not text | Still heard on en-route/terminal ops; varies by airport | https://www.sigidwiki.com/wiki/Aircraft_Communications_Addressing_and_Reporting_System_(ACARS) |
| **VDL Mode 2 (datalink)** | **136.975** CSC; **136.700–136.900** regionally | 25 kHz slots, D8PSK | **Decoder** (VDL2-specific; not voice) | Busy near major hubs; separate from voice ACARS | https://docs.airframes.io/docs/technology/vdl/intro/ |
| **VHF airband voice** | Local tower/approach/Ground (e.g. look up **KCHS**, **KATL**, **KCLT**, **KSAV** in a band plan) | 25 kHz channels (8.33 kHz not US) | **AM** | Always useful in SE; 118–137 MHz band | https://www.rtl-sdr.com/sdrsharp-users-guide/comment-page-2/ |
| **Air SAR / air-to-air** | 123.100 (SAR); 123.450 (air-to-air, remote/oceanic) | 25 kHz | **AM** | SAR and GA air-to-air | https://www.icao.int/sites/default/files/EURNAT/Documents/EUR%20and%20Nat%20Docs/EUR%20Documents/EUR%20Documents/011%20-%20EUR%20Frequency%20Management%20Manual/EUR-Doc-011-Edition-2025.pdf |
| **FM broadcast band** | 88.1–107.9 (local stations) | ~200 kHz per station | **WFM** | NOAA/public radio sidebands; propagation indicator | https://github.com/gnuradio/gnuradio/blob/master/gr-analog/python/analog/wfm_rcv_pll.py |
| **Railroad (AAR)** | **160.215–161.565** (AAR Ch 7–96, 15 kHz steps); **host-specific** (e.g. CSX **161.550** / **160.410** on RF&P-style divisions; NS road channels in 160.8–161.2 range) | 12.5–25 kHz analog voice | **NFM** | CSX/NS/Amtrak through SC/GA; use host RR when Amtrak | https://on-track-on-line.com/scanner-radio.shtml ; http://on-track-on-line.com/amtfreq-nec.shtml |
| **Part 90 land mobile** | County public safety/business UHF/VHF from **RadioReference** (many **digital P25/DMR**) | 12.5 kHz | **NFM** if analog; **Decoder** if digital | Adjacent counties may differ; many metro police encrypted | https://transition.fcc.gov/pshs/public-safety-spectrum/narrowbanding-faq.html |
| **Amateur DMR (Tier II)** | Hotspots/simplex often **446.050–446.175** (local band plans vary) | 12.5 kHz | **Decoder** (SDRTrunk, `dsd-fme`) | Growing ham digital activity | https://www.ivarc.org.uk/uploads/1/2/3/8/12380834/dmr_ivarc_slides_update_2025.pdf |
| **Ham 2 m SSB/CW calling** | 144.200 (USB calling) | ~2.7 kHz (SSB) | **USB** (not in planned WFM/NFM/AM set) | Weak-signal DX/emergency | https://www.onesdr.com/rtl-sdr-frequency-list/ |

**Notes for RTL-SDR sweeper coverage**

- All entries above sit within R820T range (no downconverter).
- **978 MHz UAT** and **1090 MHz ES** need different decoders; simultaneous reception usually needs two dongles.
- **Palmetto 800** clear P25 is already in scope; other SC systems may be P25/DMR trunked or encrypted—verify on RadioReference before expecting voice.

---

## 2. Analog demodulation: correct implementation steps

### NFM (12.5 / 25 kHz land mobile, marine, GMRS, railroad)

1. **Channelize IQ** to the signal bandwidth (typically **12.5 kHz** narrowband Part 90; **25 kHz** legacy marine/AAR; SDR# suggests **12.5 kHz** default for NFM/AM UI). — https://www.rtl-sdr.com/sdrsharp-users-guide/comment-page-2/
2. **FM discriminator** on complex baseband: instantaneous frequency from phase difference, e.g. \(\Delta\phi = \mathrm{arg}(x[n]\,x^*[n-1])\), scaled to deviation. — https://rfessentials.com/rf-knowledge-base/how-do-i-implement-a-simple-am-and-fm-demodulator-on-an-sdr-platform-for-educati/
3. **De-emphasis**: first-order low-pass (RC/digital bilinear equivalent) with time constant \(\tau\). Common practice: **~300 µs** corner (~530.5 µs in ka9q “FM/PM” preset, ~300 Hz corner) for much **VHF/UHF two-way**; **750 µs** sometimes cited for “tinny” TX audio; **200 µs** default in RTLSDR-Airband NFM. Match \(\tau\) to the service if known. — https://github.com/ka9q/ka9q-radio/blob/main/docs/ka9q-radio-3.md ; https://github.com/rtl-airband/RTLSDR-Airband/wiki/NFM-deemphasis ; https://repeater-builder.com/tech-info/pdemph-post.html
4. **Audio low-pass** to voice band (~**300 Hz–3 kHz** effective after de-emphasis; many radios band-limit before modulator). — https://www.govinfo.gov/link/cfr/47/95?link-type=pdf&sectionnum=2775&year=mostrecent (MURS audio filter requirement example)
5. **Deviation reference**: **±2.5 kHz** peak for **12.5 kHz** narrowband analog (`11K2F3E` / `11K3F3E`); legacy **±5 kHz** on **25 kHz** channels. — https://www.apcointl.org/services/radio-frequency-management/emission-designators/ ; https://transition.fcc.gov/pshs/public-safety-spectrum/narrowbanding-faq.html

### WFM (broadcast FM, 88–108 MHz)

1. **Wide channel filter** (~**150–250 kHz** RF; GNU Radio broadcast chain uses **±75 kHz** deviation design). — https://github.com/gnuradio/gnuradio/blob/master/gr-analog/python/analog/wfm_rcv_pll.py
2. **Quadrature / PLL FM demod** on channelized IQ (same phase-difference principle, scaled for **75 kHz** max deviation).
3. **Optional MPX decode** for stereo: 19 kHz pilot, 38 kHz DSB subcarrier, 57 kHz RDS; left/right de-emphasis after dematrix. — https://github.com/gnuradio/gnuradio/blob/master/gr-analog/python/analog/wfm_rcv_pll.py
4. **De-emphasis**: **75 µs** in North America (50 µs in much of Europe). Corner \(f_c = 1/(2\pi\tau) \approx 2.12\) kHz for 75 µs. — https://github.com/gnuradio/gnuradio/blob/master/gr-analog/python/analog/fm_emph.py ; https://www.fmsystems-inc.com/audio-de-emphasis-response-table/
5. **Audio bandwidth**: MPX low-pass ~**15 kHz** before de-emphasis in typical broadcast receivers. — https://github.com/gnuradio/gnuradio/blob/master/gr-analog/python/analog/wfm_rcv_pll.py

### AM (aviation voice, ACARS carrier, MF/HF broadcast)

1. **Channelize** to channel bandwidth (~**8–12.5 kHz** for airband voice; **~10 kHz** often quoted for AM BCB; SDR# default **12.5 kHz** for AM/NFM). — https://www.rtl-sdr.com/sdrsharp-users-guide/comment-page-2/ ; http://zigradio.org/examples/rtlsdr-am-envelope.html
2. **Demodulation**: **envelope** \(|I+jQ|\) (non-coherent AM) or **coherent/synchronous** AM (lock to carrier for fading resilience). — http://zigradio.org/examples/rtlsdr-am-envelope.html ; https://www.rtl-sdr.com/sdrsharp-users-guide/comment-page-2/
3. **DC removal**: high-pass (~**100 Hz**) after envelope to strip residual carrier/LO offset. — http://zigradio.org/examples/rtlsdr-am-envelope.html
4. **Audio low-pass** to ~**5 kHz** (or lower for narrow aviation channels). — http://zigradio.org/examples/rtlsdr-am-envelope.html ; https://github.com/nktkt/rtlsdr-am-fm-demod
5. **Deviation**: not applicable; depth is **amplitude modulation** (typical voice on VHF airband uses AM with defined occupied bandwidth, not FM deviation).

---

## 3. Clear digital voice: P25, DMR, JMBE (no encryption bypass)

**P25 (Project 25)** — North American public-safety digital standard (TIA-102). Phase 1 uses **12.5 kHz FDMA**, **C4FM** at **4800 symbols/s** (9600 bit/s), **IMBE** vocoder (~4.4 kb/s speech + FEC). Phase 2 adds **2-slot TDMA** and **AMBE+2** on the same RF channelization. An external trunk decoder (**SDRTrunk**) ingests **wideband IQ** from the SDR, tracks control channels, demodulates **C4FM/CQPSK**, extracts **IMBE/AMBE frames**, then plays PCM. Feeding **analog NFM audio to speakers** only reproduces a **9600 bit/s four-level FSK waveform** as noise/buzz—it does not run sync, FEC, or the vocoder, so speech cannot appear. — https://en.wikipedia.org/wiki/Project_25 ; https://gophertrunk.org/blog/deep-dives/p25-end-to-end-01-c4fm-carrier/ ; https://www.taitradioacademy.com/courses/intro-to-p25/trg-00001-01-m_intro_to_p25-2/

**DMR (Digital Mobile Radio)** — ETSI **TS 102 361** professional/ham digital standard: **12.5 kHz** RF, **2-slot TDMA**, **4FSK** at **4800 symbols/s** (9600 bit/s channel), industry-de-facto **AMBE+2** half-rate vocoder (~3.6 kb/s including FEC). Decoders (**SDRTrunk**, **dsd-fme** with `-ft`/`-fs`, **DSDPlus**) expect **discriminator-style 48 kHz mono PCM** (or IQ in SDRTrunk) to recover **dibits**, frame sync, and slot multiplexing before vocoding. Analog **NFM** without symbol timing and 4FSK slicing produces unintelligible audio because voice lives in **compressed digital bursts**, not continuous FM speech. — https://www.dmrassociation.org/public-downloads/documents/White-Papers/DMR-Association-White-Paper_Benefits-and-Features-of-DMR_160512.pdf ; https://github.com/lwvmobile/dsd-fme/blob/audio_work/examples/Example_Usage.md

**JMBE (Java Multi-Band Excitation library)** — Not a over-the-air protocol; a **vocoder library** used by **SDRTrunk** to turn **IMBE** (P25 Phase 1) and **AMBE** (P25 Phase 2, DMR, NXDN) **codewords** into **8 kHz 16-bit mono PCM**. SDRTrunk **0.6.1+** expects **JMBE 1.0.9+** (path set under Preferences → Decoder → JMBE). It sits **after** bit-level decoding; without it, P25/DMR metadata may decode but **voice stays silent**. JMBE does not decrypt AES or other encrypted payloads. — https://github.com/DSheirer/jmbe/blob/master/README.md ; https://github.com/DSheirer/sdrtrunk/wiki/JMBE ; https://github.com/DSheirer/sdrtrunk/releases/tag/v0.6.1

### External decoder I/O cheat sheet

| Tool | Typical input | Role |
|------|---------------|------|
| **SDRTrunk** | IQ from RTL-SDR (wideband, multi-MS/s); internal P25/DMR stack + **JMBE** | Trunk tracking + clear voice |
| **dsd-fme** | **48 kHz** mono PCM (TCP/`rtl_fm -s 48k -M fm`, WAV); optional IQ via `-i rtl` | P25/DMR/NXDN/ProVoice frame decode + mbe path |
| **multimon-ng** | **22050 Hz** raw signed 16-bit (`rtl_fm \| multimon-ng -t raw`) | POCSAG/FLEX/AFSK/DSC-class FSK, not voice vocoders |
| **dump978-fa** | IQ @ **978 MHz**, ~**2.083 MS/s** | UAT ADS-B |
| **acarsdec** / VDL tools | AM-discriminator or IQ at ACARS/VDL freqs | Aviation data messages |

— https://github.com/EliasOenal/multimon-ng/ ; https://deepwiki.com/lwvmobile/dsd-fme/2.1-audio-input-and-symbol-extraction ; https://github.com/wiedehopf/dump978

---

## References (general)

- RTL-SDR band/use overview: https://www.onesdr.com/rtl-sdr-frequency-list/
- R820T frequency range: https://osmocom.org/projects/rtl-sdr/wiki/
- GopherTrunk “other signals” primer: https://gophertrunk.org/learn/rf-sdr/other-signals/
