import argparse
import logging
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from bs4.element import ResultSet, Tag
from PyPDF2 import PdfMerger
from requests import Session


class Document:
    tag: Tag

    def __init__(self, tag: Tag):
        self.tag = tag

    @property
    def name(self) -> str:
        return self.tag.a.text

    @property
    def href(self) -> str:
        return self.tag.a['href']


class DocumentDownloader:
    base_url: str = 'https://www.aip.net.nz/'

    # soup.find(attrs={'class': 'home__browse-section'}).find_all('a')
    sections: dict[str, str] = {
        'GEN': '/document-category/General-GEN',
        'ENR': '/document-category/En-route-ENR',
        'AD': '/document-category/Aerodromes-AD1',
        'SUP': '',
    }

    session: Session

    logger: logging.Logger

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

        self.session = requests.session()
        self.session.cookies['disclaimer'] = '1'

    def download_document(self, document: Document, dest_file: Path) -> None:
        document_url = urljoin(self.base_url, document.href)
        with self.session.get(document_url, stream=True) as resp:
            resp.raise_for_status()

            if dest_file.exists():
                self.logger.warning(f'Updating "{document.name}"')

            self.logger.info(f'Downloading from "{document_url}"')
            with open(dest_file, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=4096):
                    f.write(chunk)

            last_mod_header = resp.headers['Last-Modified']
            if last_mod_header:
                src_last_mod = datetime.strptime(last_mod_header, '%a, %d %b %Y %H:%M:%S %Z')

                self.logger.info(f'Updating "{document.name}" timestamp to "{src_last_mod}"')
                os.utime(dest_file, (src_last_mod.timestamp(), src_last_mod.timestamp()))

    def document_is_up_to_date(self, document: Document, dest_file: Path) -> bool:
        document_effective_match = re.match(r'.* effective (\d{1,2})(?: to \d{1,2})?( \w+ \d{4})', document.name)
        if document_effective_match:
            self.logger.info(f'Checking "{document.name}" effective date')

            document_effective = document_effective_match.group(1) + document_effective_match.group(2)
            effective_date = datetime.strptime(document_effective, '%d %B %Y')

            if datetime.utcnow() < effective_date:
                self.logger.warning(f'Skipping "{document.name}" as it is not yet effective')
                return True

        document_url = urljoin(self.base_url, document.href)
        with self.session.head(document_url) as head:
            head.raise_for_status()

            if head.headers['Content-Type'] != 'application/pdf':
                self.logger.error(f'Skipping "{document.name}" as it has an invalid content type: "{head.headers["Content-Type"]}"')
                return True

            src_last_mod = None
            last_mod_header = head.headers['Last-Modified']
            if last_mod_header:
                src_last_mod = datetime.strptime(last_mod_header, '%a, %d %b %Y %H:%M:%S %Z')

            dest_ctime = None
            dest_exists = dest_file.exists()
            if dest_exists:
                dest_ctime = datetime.fromtimestamp(dest_file.stat().st_ctime)

            if dest_ctime and src_last_mod:
                if dest_ctime > src_last_mod:
                    self.logger.info(f'Skipping "{document.name}" as it is up to date')
                    return True

        return False

    def get_tags(self, section_name: str) -> ResultSet:
        source_url = urljoin(self.base_url, self.sections[section_name])
        self.logger.info(f'Retrieving tags from "{source_url}"')

        with self.session.get(source_url) as resp:
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, 'html5lib')

            if section_name == 'SUP':
                return soup.find(
                    'div', attrs={'class': 'home__block-title'}, string='Additional documents'
                ).parent.find_all(
                    'li', attrs={'class': 'home__popular-amendment-item'}
                )

            return soup.find_all('div', attrs={'class': 'file-info'})

    def get_documents(self, section_name: str) -> List[Document]:
        return [Document(tag) for tag in self.get_tags(section_name)]

    def sync(self, target_path: Path) -> None:
        self.logger.info(f'Syncing AIP to "{target_path}"')
        target_path.mkdir(parents=True, exist_ok=True)

        aip_bundle = PdfMerger()
        aip_bundle.add_metadata({'/Title': 'AIP New Zealand'})

        for section in self.sections:
            section_path = target_path / section

            if section == 'SUP' and section_path.exists():
                self.logger.info('Cleaning up AIP supplements')
                shutil.rmtree(section_path, ignore_errors=True)

            section_path.mkdir(parents=True, exist_ok=True)

            for document in self.get_documents(section):
                dest_file = section_path / Path(document.href).name
                self.logger.info(f'Downloading "{document.name}" to "{dest_file}"')

                if self.document_is_up_to_date(document, dest_file):
                    continue

                try:
                    self.download_document(document, dest_file)
                except Exception as e:
                    self.logger.error(f'Failed to download "{document.name}": {e}')
                    continue

                bookmark_name = document.name if section == 'SUP' else f'{section} {document.name}'
                self.logger.info(f'Adding "{bookmark_name}" to AIP bundle')
                aip_bundle.append(str(dest_file), bookmark_name)

        bundle_path = target_path / 'AIP New Zealand.pdf'
        self.logger.info(f'Writing AIP bundle to "{bundle_path}"')
        aip_bundle.write(bundle_path)
        aip_bundle.close()


def main(argv: List[str] = None) -> int:
    # Parse arguments
    parser = argparse.ArgumentParser(description='Download AIP New Zealand')
    parser.add_argument('-q', '--quiet', action='store_true', help='Disable logging')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose logging')

    parser.add_argument('-d', '--dest', type=Path, default=Path('AIP'), help='Destination path (default: AIP)')
    args = parser.parse_args(argv)

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO if not args.quiet else logging.WARNING,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # Download AIP
    DocumentDownloader().sync(args.dest)
    return 0


if __name__ == '__main__':
    sys.exit(main())
