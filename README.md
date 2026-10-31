# REA One Shot

Bir uygulama dosyasını, paketi veya web sayfasını **tek komutla** REA üzerinden ilk incelemeye alır; ham kanıtı, işlem kaydını ve okunabilir bir raporu saklar. Agent skill’i, bu ilk incelemeden sonra sorunuzla ilgili fonksiyonları ve davranışı izleyerek kanıta dayalı açıklama hazırlar.

> [!NOTE]
> Bu proje [REA](https://github.com/morluto/rea) üzerine kurulmuş bağımsız bir iş akışıdır. REA’nın resmî dağıtımı değildir. Yerel programları çalıştırmaz; web adresi verirseniz o sayfayı tarayıcıda açar.

## Skill’i kur

Python 3.10+ ve REA'nın desteklediği Node.js sürümü (`^22.23.4 || ^24.22.1 || >=26.12.1`) yüklüyse:

```bash
npx skills add cevo1466/Tersine-M-hendislik --skill rea-one-shot
```

Codex veya başka bir uyumlu agent içinde şu şekilde çağırın:

```text
$rea-one-shot /absolute/path/to/application.asar: Arama özelliği nasıl çalışıyor? Kanıtları göster.
```

`skills add` skill talimatlarını kurar. REA kurulumunu veya analiz motorlarını kurmaz. Skill, ilk inceleme için kendi `scripts/rea_once.py` dosyasını kullanır; `rea` kurulu değilse sabitlenmiş `rea-agents@6.8.0` sürümünü `npx` ile çağırır. Terminal örnekleri depo kökünden çalıştırılır; skill kurulumu sonrası agent `$rea-one-shot` çağrısı kurulu dosyayı kendi konumundan bulur.

## Terminalden tek komut

Depoyu klonladıktan sonra:

```bash
python3 skills/rea-one-shot/scripts/rea_once.py /absolute/path/to/target --output ./rea-report
```

Örnekler:

```bash
python3 skills/rea-one-shot/scripts/rea_once.py ./app.asar --output ./report-js
python3 skills/rea-one-shot/scripts/rea_once.py ./program --provider ghidra --output ./report-native
python3 skills/rea-one-shot/scripts/rea_once.py ./application.apk --android-code --output ./report-apk
python3 skills/rea-one-shot/scripts/rea_once.py https://example.com --output ./report-web
```

Çıktı klasöründe `manifest.json`, `report.md`, her adımın REA JSON çıktısı ve stderr kaydı bulunur. Mevcut çıktı klasörünün üzerine yazılmaz. Bir adım başarısız olsa bile mevcut kayıtlar tutulur ve komut başarısız çıkış kodu döner.

## Gereksinimler ve kapsam

- Python 3.10+; REA için desteklenen Node.js sürümü: `^22.23.4 || ^24.22.1 || >=26.12.1`.
- Yerel native kodun derin analizi için önceden yapılandırılmış Ghidra, Hopper, IDA veya Kuna gerekir. REA tek uygun sağlayıcıyı seçebilir; birden çok sağlayıcı varsa `--provider` kullanın. Analiz başarısızsa komut dosya envanterini ayrıca kaydeder ve `partial` durumuyla çıkar.
- APK kodu için `--android-code`, önceden yapılandırılmış JADX ve tam JDK gerekir. APK envanteri ve imzası bunlar olmadan incelenebilir.
- Web adresi için kurulu Chrome tabanlı bir tarayıcı gerekir. Tarayıcı bulunamazsa `--browser-executable` kullanın.
- JavaScript/Electron klasörleri ve ASAR dosyaları statik olarak analiz edilir. Linux’ta bu işlem için `ps` gereklidir.

Başlangıç raporu genel kapsamı gösterir. Belirli bir özelliğin mekanizmasını kanıtlamak için skill içindeki takip analizi kullanın. REA’nın kendi [CLI rehberi](https://github.com/morluto/rea/blob/main/docs/cli.md) ve [kurulum rehberi](https://github.com/morluto/rea/blob/main/docs/installation.md) ek araçları açıklar.

## Geliştirme

```bash
python3 -m unittest discover -s tests -v
python3 /path/to/skill-creator/scripts/quick_validate.py skills/rea-one-shot
```

MIT lisansı. REA ayrı bir MIT lisanslı projedir.
