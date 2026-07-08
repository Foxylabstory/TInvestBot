Положите сюда два файла, скачанных с официального источника Минцифры/Госуслуг:

1. russian_trusted_root_ca.cer
   https://gu-st.ru/content/Other/doc/russian_trusted_root_ca.cer

2. russian_trusted_sub_ca.cer
   https://gu-st.ru/content/Other/doc/russian_trusted_sub_ca.cer

Оба файла нужны для подключения к T-Invest API (invest-public-api.tbank.ru),
который переходит на TLS-сертификаты НУЦ Минцифры — без этих файлов
aiohttp не сможет проверить сертификат сервера и будет выдавать
SSLCertVerificationError.

После скачивания у вас должно получиться:
  certs/russian_trusted_root_ca.cer
  certs/russian_trusted_sub_ca.cer

Этот файл (README_CERTS.txt) можно оставить в папке — он ни на что не влияет.