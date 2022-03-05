import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Union, List
from urllib.parse import urljoin

import requests
from PyPDF2 import PdfFileMerger
from bs4 import BeautifulSoup
from requests import Session


def download_document(url: str, dest: Union[Path, str], session: Session = None) -> bool:
    if not Session:
        session = requests.session()

    if isinstance(dest, str):
        dest = Path(dest)

    dest_exists = dest.exists()
    dest_ctime = datetime.fromtimestamp(dest.stat().st_ctime) if dest_exists else datetime.fromtimestamp(0)
    src_last_mod = datetime.fromtimestamp(0)

    head = session.head(url)

    if head.headers['Content-Type'] != 'application/pdf':
        raise Exception(f'Invalid content type: "{head.headers["Content-Type"]}"')

    r_last_mod = head.headers['Last-Modified']
    if r_last_mod:
        src_last_mod = datetime.strptime(r_last_mod, '%a, %d %b %Y %H:%M:%S %Z')
        if dest_ctime > src_last_mod:
            print('already downloaded')
            return False

    with session.get(url, stream=True) as resp:
        resp.raise_for_status()

        if dest_exists:
            print('downloading update: ', end='')

        print(f'downloading from "{url}": ', end='')
        with open(dest, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=4096):
                f.write(chunk)

        if r_last_mod:
            os.utime(dest, (src_last_mod.timestamp(), src_last_mod.timestamp()))
        print('done')


def main(argv: List[str] = None) -> int:
    download_dir = Path('AIP')
    download_dir.mkdir(parents=True, exist_ok=True)

    base_url = 'https://www.aip.net.nz/'
    sess = Session()
    sess.cookies['disclaimer'] = '1'

    aip = PdfFileMerger()
    aip.addMetadata({'/Title': 'AIP New Zealand'})

    # aip_sections = soup.find(attrs={'class': 'home__browse-section'}).find_all('a')
    for section_name, section_href in [
        ('GEN', '/document-category/General-GEN'),
        ('ENR', '/document-category/En-route-ENR'),
        ('AD', '/document-category/Aerodromes-AD1'),
        ('SUP', ''),
    ]:
        resp = sess.get(urljoin(base_url, section_href))
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, 'html5lib')

        section_dir = download_dir / section_name
        section_dir.mkdir(parents=True, exist_ok=True)

        documents = soup.find_all('div', attrs={'class': 'file-info'}) if section_name != 'SUP' else soup.find('div', attrs={
            'class': 'home__block-title'}, text='Additional documents').parent.find_all('li', attrs={
            'class': 'home__popular-amendment-item'})
        for document in documents:
            document_name = document.a.text if section_name != 'SUP' else document.div.a.text
            print(f'Processing "{section_name} {document_name}": ', end='')

            document_href = document.a['href'] if section_name != 'SUP' else document.div.a['href']
            if not document_href:
                print('missing href url')
                continue

            src_url = urljoin(base_url, document_href)
            dest_file = section_dir / Path(document_href).name if section_name != 'SUP' else section_dir / (document_name + '.pdf')

            download_document(src_url, dest_file, sess)

            if section_name == 'SUP':
                document_effective = re.match(r'.* effective (\d+ \w+ \d+)', document_name)
                if not document_effective:
                    continue

                effective_date = datetime.strptime(document_effective.group(1), '%d %B %Y')

                if datetime.utcnow() < effective_date:
                    continue

            aip.append(str(dest_file), f'{section_name} {document_name}' if section_name != 'SUP' else document_name)

    aip.write(str(download_dir / 'AIP New Zealand.pdf'))

    return 0


if __name__ == '__main__':
    sys.exit(main())
