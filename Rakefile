if Rake::Win32.windows? then
  PYTHON='python'
  LS='dir'
else
  PYTHON='python3'
  LS='ls -l'
end

desc 'Show latest HEAD with stats'
task :what do
  sh 'git log -3 --stat HEAD'
end

desc 'Empty and create tmp directory.'
task :tmp do
    rm_rf('tmp')
    mkdir('tmp')
end

desc 'Setup build environment RobotFW32'
task :envsetup => :tmp do
    sh "#{PYTHON} -m pip install -r requirements-rf32.txt"
    sh "#{PYTHON} -m pip install -r devrequirements.txt"
    sh "#{PYTHON} -m pip install -e ."
    sh "#{PYTHON} -m pip freeze"
end

desc 'Setup build environment RobotFW40'
task :envsetup40 => :tmp do
    sh "#{PYTHON} -m pip install -r requirements-rf40.txt"
    sh "#{PYTHON} -m pip install -r devrequirements.txt"
    sh "#{PYTHON} -m pip install -e ."
    sh "#{PYTHON} -m pip freeze"
end

desc 'Clean workspace from trash files.'
task :clean do
    rm_rf 'build/'
    rm_rf 'dist/'
    rm_rf '*.pyc'
    rm_rf '__pycache__/'
end

desc 'Run unittests for this project.'
task :test => :tmp do
    sh "#{PYTHON} -W ignore::FutureWarning -m pytest -q --cov-branch --cov=src/robotkernel --cov-report=term-missing src/robotkernel tests"
end

desc 'Run tests, pylint and generate package.'
task :package => :test do
    sh "#{PYTHON} setup.py bdist_wheel"
    sh "#{LS} dist"
end

