from setuptools import setup, find_packages
import os

with open(os.path.join(os.path.dirname(__file__), '../README.md'), 'r') as f:
    long_description = f.read()

setup(
    name='webai2api',
    version='0.1.4',
    description='Web API for AI models',
    author='Amm1rr',
    author_email='soheyl637@gmail.com',
    url='https://github.com/amm1rr/WebAI-to-API',
    packages=find_packages(),
    install_requires=[
        'fastapi==0.111.0',
        'uvicorn==0.15.0',
        'browser_cookie3==0.19.1',
        'httpx==0.27.0',
        'gemini-webapi==1.2.0',
        'curl_cffi==0.6.3',
        'httptools>=0.5.0'
    ],
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Build Tools',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Framework :: FastAPI',
        'Framework :: Uvicorn',
        'Operating System :: OS Independent',
        'Natural Language :: English'
    ],
    python_requires='>=3.10',
    license='MIT',
    long_description=long_description,
    long_description_content_type='text/markdown',
    keywords=['web', 'API', 'AI', 'models']
)
