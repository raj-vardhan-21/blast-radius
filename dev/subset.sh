#!/bin/sh
# Make DIR_lab with only the labeled commits of DIR (for scoring partial labels).
set -e
D=$1; O=${D}_lab
rm -rf $O && mkdir -p $O/commits && cp $D/answers.json $D/tests.json $O/
python3 -c "
import json,shutil
for k in json.load(open('$D/answers.json'))['commits']: shutil.copy('$D/commits/'+k+'.patch','$O/commits/')"
ls $O/commits | wc -l
