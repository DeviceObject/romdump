#!/usr/bin/env python

import sys
import os
from struct import unpack

import guids as g

#fvh_count = 0


### Formatting: GUIDs
def format_guid(guid_s):
    parts = unpack('<LHH8B', guid_s)
    return '%08X-%04X-%04X-%02X%02X-%02X%02X%02X%02X%02X%02X' % parts


### Formatting: file types
filetypes = ('ALL ', 'RAW ', 'FREE', 'SECc', 'PEIc', 'DXEc', 'PEIM', 'DRVR', 'CPDR', 'APPL', '??0A', 'FVIM')
filetype_exts = ('all', 'raw', 'free', 'seccore', 'peicore', 'dxecore', 'peim', 'drv', 'comb_peim_drv', 'app', 'unkn0a',
                 'fd')


def format_filetype(filetype_num):
    if filetype_num < len(filetypes):
        return filetypes[filetype_num]
    return '??%02X' % filetype_num


def extention_filetype(filetype_num):
    if filetype_num < len(filetype_exts):
        return filetype_exts[filetype_num]
    return 'unkn%02x' % filetype_num


### Main function to analyze a host disk file
def analyze_diskfile(filename):
    f = file(filename, 'rb')
    fvdata = f.read()
    f.close()

    print 'Analyzing %s, 0x%X bytes' % (filename, len(fvdata))

    if fvdata[0:16] == g.EFI_CAPSULE_GUID.bytes_le:
        # EFI capsule
        (capguid, capheadsize, capflags, capimagesize, capseqno, capinstance, capsplitinfooffset, capbodyoffset,
         cap3offset, cap4offset, cap5offset, cap6offset, cap7offset, cap8offset) = unpack('<16sLLLL16sLL6L',
                                                                                          fvdata[0:80])
        print 'EFI Capsule, %d bytes' % capimagesize
        handle_fv(fvdata[capbodyoffset:capbodyoffset + capimagesize], format_guid(capguid))

    else:
        # treat as sequence of firmware volumes
        while True:
            usedsize = handle_fv(fvdata)
            if usedsize >= len(fvdata):
                break
            else:
                fvdata = fvdata[usedsize:]


### Handle a firmware volume
def handle_fv(fvdata, name='default'):

    ### Check header
    (fvzero, fvfstype, fvlen, fvsig, fvattr, fvhdrlen, fvchecksum, fvrev) = unpack('<16s16sQ4sLHH3xB', fvdata[0:0x38])
    if fvsig != '_FVH':
        print 'Not a EFI firmware volume (sig missing)'
        return 0
    if fvlen > len(fvdata):
        print 'WARNING: File too short, header gives length as 0x%X bytes' % fvlen
    else:
        print 'Size per header: 0x%X bytes' % fvlen
    offset = fvhdrlen

#    global fvh_count
#    fvhdir = 'fvh-%d' % fvh_count
#    fvh_count += 1
    fvhdir = 'fvh-' + name
    os.mkdir(fvhdir)
    os.chdir(fvhdir)

    ### Decode files

    print 'Listing files'
    print '-----'

    while True:
        if offset == fvlen:
            print '-----'
            print 'End of volume (size reached cleanly)'
            break
        if offset + 24 > fvlen:
            print '-----'
            print 'End of volume (size reached uncleanly)'
            break

        (fileguid, fileintcheck, filetype, fileattr, filelenandstate) = unpack('<16sHBBL', fvdata[offset:offset + 24])
        if filetype == 0xff:
            print '-----'
            print 'End of volume (filler data found)'
            break
        fileentrylen = filelenandstate & 0xffffff
        filestate = filelenandstate >> 24
        filelen = fileentrylen - 24
        if fileattr & 1:
            print '  has tail!'
            filelen -= 2

        fileoffset = offset + 24
        nextoffset = (offset + fileentrylen + 7) & ~7

        filedata = fvdata[fileoffset:fileoffset + filelen]
        compressed = False
        if filetype != 1 and filedata[3] == "\x01":
            compressed = True
            filedata = decompress(filedata)

        if compressed:
            print '%s  %s  C %d (%d)' % (format_guid(fileguid), format_filetype(filetype), len(filedata), filelen)
        else:
            print '%s  %s  U %d' % (format_guid(fileguid), format_filetype(filetype), filelen)

        handle_file('file-%s.%s' % (format_guid(fileguid), extention_filetype(filetype)), filetype, filedata)

        offset = nextoffset

    os.chdir('..')
    return fvlen


### Handle decompression of a compressed section
def decompress(compdata):
    (sectlenandtype, uncomplen, comptype) = unpack("<LLB", compdata[0:9])
    sectlen = sectlenandtype & 0xffffff
    if sectlen < len(compdata):
        print 'WARNING: Compressed section is not the only section! (%d/%d)' % (sectlen, len(compdata))
    if comptype == 0:
        return compdata[9:]
    elif comptype == 1:
        print 'WARNING: this code path might not work'
        f = file('_tmp_decompress', 'wb')
        f.write(compdata[9:])
        f.close()

        os.system('./efidecomp <_tmp_decompress >_tmp_result')

        f = file('_tmp_result', 'rb')
        decompdata = f.read()
        f.close()

        if len(decompdata) < uncomplen:
            print 'WARNING: Decompressed data too short!'
        return decompdata

    elif comptype == 2:
        f = file('_tmp_decompress', 'wb')
        f.write(compdata[13:sectlen + 4])  # for some reason there is junk in 9:13 that I don't see in the raw files?!
        f.close()

        os.system('lzmadec <_tmp_decompress >_tmp_result')

        f = file('_tmp_result', 'rb')
        decompdata = f.read()
        f.close()

        if len(decompdata) < uncomplen:
            print 'WARNING: Decompressed data too short!'
        return decompdata
    else:
        print 'ERROR: Unknown compression type %d' % comptype
        return compdata


### Handle the contents of one firmware file
def handle_file(filename, filetype, filedata):
    if filetype == 1:
        f = file('%s.raw' % filename, 'wb')
        f.write(filedata)
        f.close()
    if filetype != 1:
        handle_sections(filename, 0, filedata)


### Handle section data (i.e. multiple sections), recurse if necessary
def handle_sections(filename, sectindex, imagedata):
    imagelen = len(imagedata)
    filename_override = None

    # first try to find a filename
    offset = 0
    while offset + 4 <= imagelen:
        (sectlenandtype,) = unpack('<L', imagedata[offset:offset + 4])
        sectlen = sectlenandtype & 0xffffff
        secttype = sectlenandtype >> 24
        nextoffset = (offset + sectlen + 3) & ~3
        dataoffset = offset + 4
        datalen = sectlen - 4
        sectdata = imagedata[dataoffset:dataoffset + datalen]

        if secttype == 0x15:
            filename_override = sectdata[:-2].decode('utf-16le').encode('utf-8')
            print "  Filename '%s'" % filename_override

        offset = nextoffset

    # then analyze the sections for good
    offset = 0
    while offset + 4 <= imagelen:
        (sectlenandtype,) = unpack('<L', imagedata[offset:offset + 4])
        sectlen = sectlenandtype & 0xffffff
        secttype = sectlenandtype >> 24
        nextoffset = (offset + sectlen + 3) & ~3
        dataoffset = offset + 4
        datalen = sectlen - 4

        if secttype == 2:
            (sectguid, sectdataoffset, sectattr) = unpack('<16sHH', imagedata[offset + 4:offset + 24])
            dataoffset = offset + sectdataoffset
            datalen = sectlen - sectdataoffset
            if sectguid == g.EFI_SECTION_CRC32_GUID.bytes_le:
                # CRC32 section
                sectindex = handle_sections(filename, sectindex, imagedata[dataoffset:dataoffset + datalen])
            else:
                print '  %02d  GUID %s' % (sectindex, format_guid(sectguid))
                sectindex += 1
                sectindex = handle_sections(filename, sectindex, imagedata[dataoffset:dataoffset + datalen])
        else:
            secttype_name = 'UNKNOWN(%02X)' % secttype
            ext = 'data'
            sectdata = imagedata[dataoffset:dataoffset + datalen]
            extraprint = ''

            if secttype == 0x10:
                secttype_name = 'PE32'
                ext = 'efi'
            elif secttype == 0x11:
                secttype_name = 'PIC'
                ext = 'pic.efi'
            elif secttype == 0x12:
                secttype_name = 'TE'
                ext = 'te'
            elif secttype == 0x13:
                secttype_name = 'DXE_DEPEX'
                ext = None
            elif secttype == 0x14:
                secttype_name = 'VERSION'
                ext = None
            elif secttype == 0x15:
                secttype_name = 'USER_INTERFACE'
                ext = None
            elif secttype == 0x16:
                secttype_name = 'COMPATIBILITY16'
                ext = 'bios'
            elif secttype == 0x17:
                secttype_name = 'FIRMWARE_VOLUME_IMAGE'
                ext = 'fd'
            elif secttype == 0x18:
                secttype_name = 'FREEFORM_SUBTYPE_GUID'
                ext = None
            elif secttype == 0x19:
                secttype_name = 'RAW'
                ext = 'raw'
                if sectdata[0:8] == "\x89PNG\x0D\x0A\x1A\x0A":
                    ext = 'png'
                elif sectdata[0:4] == 'icns':
                    ext = 'icns'
            elif secttype == 0x1B:
                secttype_name = 'PEI_DEPEX'
                ext = None

            print '  %02d  %s  %d%s' % (sectindex, secttype_name, datalen, extraprint)

            if ext is not None:
                use_filename = '%s-%02d' % (filename, sectindex)
                if filename_override is not None:
                    use_filename = filename_override
                f = file('%s.%s' % (use_filename, ext), 'wb')
                f.write(sectdata)
                f.close()

            if secttype == 0x17:
                print '*** Recursively analyzing the contained firmware volume...'
                handle_fv(sectdata, filename)

            sectindex += 1

        offset = nextoffset

    return sectindex


### main code
def main():
    if len(sys.argv) > 1:
        for filename in sys.argv[1:]:
            analyze_diskfile(filename)
    else:
        print 'No file specified, giving up'


if __name__ == '__main__':
    main()
