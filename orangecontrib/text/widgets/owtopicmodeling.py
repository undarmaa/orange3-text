from PyQt4 import QtGui
from PyQt4.QtGui import QVBoxLayout, QButtonGroup, QRadioButton
from PyQt4 import QtCore
from Orange.widgets.widget import OWWidget
from Orange.widgets import settings
from Orange.widgets import gui
from Orange.data import Table
from Orange.widgets.data.contexthandlers import DomainContextHandler
from orangecontrib.text.corpus import Corpus
from orangecontrib.text.topics import Topics, LdaWrapper, HdpWrapper, LsiWrapper


class TopicWidget(gui.OWComponent, QtGui.QGroupBox):
    Model = NotImplemented
    valueChanged = QtCore.pyqtSignal(object)

    def __init__(self, master, **kwargs):
        QtGui.QGroupBox.__init__(self, **kwargs)
        gui.OWComponent.__init__(self, master)
        self.model = self.create_model()

    def on_change(self):
        self.model = self.create_model()
        self.valueChanged.emit(self)

    def create_model(self):
        raise NotImplementedError


class LdaWidget(TopicWidget):
    Model = LdaWrapper
    num_topics = settings.Setting(10)

    def __init__(self, widget, **kwargs):
        super().__init__(widget, **kwargs)
        QVBoxLayout(self)
        spin = gui.spin(self, self, 'num_topics', minv=1, maxv=1000, step=1,
                        label='Number of topics: ')
        spin.editingFinished.connect(self.on_change)

    def create_model(self):
        return self.Model(num_topics=self.num_topics)


class LsiWidget(TopicWidget):
    Model = LsiWrapper
    num_topics = settings.Setting(10)

    def __init__(self, widget, **kwargs):
        super().__init__(widget, **kwargs)
        QVBoxLayout(self)
        spin = gui.spin(self, self, 'num_topics', minv=1, maxv=1000, step=1,
                        label='Number of topics: ')
        spin.editingFinished.connect(self.on_change)

    def create_model(self):
        return self.Model(num_topics=self.num_topics)


class HdpWidget(TopicWidget):
    Model = HdpWrapper

    parameters = {
        'gamma': 'First level concentration',
        'alpha': 'Second level concentration',
        'eta': 'The topic Dirichlet',
        'T': 'Top level truncation level',
        'K': 'Second level truncation level',
        'kappa': 'Learning rate',
        'tau': 'Slow down parameter'
    }
    gamma = settings.Setting(1)
    alpha = settings.Setting(1)
    eta = settings.Setting(.01)
    T = settings.Setting(150)
    K = settings.Setting(15)
    kappa = settings.Setting(1)
    tau = settings.Setting(64)

    def __init__(self, widget, **kwargs):
        super().__init__(widget, **kwargs)
        QVBoxLayout(self)
        for parameter, description in self.parameters.items():
            spin = gui.spin(self, self, parameter, minv=0, maxv=100, step=.1,
                            labelWidth=220, label='{} ({}):'.format(description, parameter),
                            spinType=int)
            spin.editingFinished.connect(self.on_change)

    def create_model(self):
        return self.Model(**{param: getattr(self, param) for param in self.parameters})


class Output:
    DATA = "Data"
    TOPICS = "Topics"


class OWTopicModeling(OWWidget):
    name = "Topic Modelling"
    description = "Uncover the hidden thematic structure in a corpus."
    icon = "icons/TopicModeling.svg"
    priority = 50

    settingsHandler = DomainContextHandler()

    # Input/output
    inputs = [("Corpus", Corpus, "set_data")]
    outputs = [(Output.DATA, Table),
               (Output.TOPICS, Topics)]
    want_main_area = True

    methods = [
        (LsiWidget, 'lsi'),
        (LdaWidget, 'lda'),
        (HdpWidget, 'hdp'),
    ]

    # Settings
    autocommit = settings.Setting(True)
    method_index = settings.Setting(0)

    lsi = settings.SettingProvider(LsiWidget)
    hdp = settings.SettingProvider(HdpWidget)
    lda = settings.SettingProvider(LdaWidget)

    def __init__(self):
        super().__init__()
        self.apply_mutex = QtCore.QMutex()
        self.corpus = None
        self.learning_thread = None

        button_group = QButtonGroup(self, exclusive=True)
        button_group.buttonClicked[int].connect(self.change_method)

        self.widgets = []
        method_layout = QVBoxLayout()
        self.controlArea.layout().addLayout(method_layout)
        for i, (method, attr_name) in enumerate(self.methods):
            widget = method(self, title='Options')
            widget.valueChanged.connect(self.commit)
            self.widgets.append(widget)
            setattr(self, attr_name, widget)

            rb = QRadioButton(text=widget.Model.name)
            button_group.addButton(rb, i)
            method_layout.addWidget(rb)
            method_layout.addWidget(widget)

        button_group.button(self.method_index).setChecked(True)
        self.toggle_widgets()
        method_layout.addStretch()

        # self.update_button = gui.button(self, self, 'Update', callback=self.update_model)
        # self.controlArea.layout().addWidget(self.update_button)

        # Commit button
        gui.auto_commit(self.buttonsArea, self, 'autocommit', 'Commit', box=False)

        # Topics description
        self.topic_desc = TopicViewer()
        self.topic_desc.topicSelected.connect(self.send_topic_by_id)
        self.mainArea.layout().addWidget(self.topic_desc)

    def set_data(self, data=None):
        self.corpus = data.copy()
        self.apply()

    def commit(self):
        if self.corpus is not None:
            self.apply()

    @property
    def model(self):
        return self.widgets[self.method_index].model

    def change_method(self, new_index):
        if self.method_index != new_index:
            self.method_index = new_index
            self.toggle_widgets()
            self.commit()

    def toggle_widgets(self):
        for i, widget in enumerate(self.widgets):
            widget.setVisible(i == self.method_index)

    def update_topics(self):
        self.topic_desc.show_model(self.model)

    def progress(self, p):
        self.progressBarSet(p)

    def apply(self):
        self.stop_learning()
        if self.corpus:
            self.start_learning()
        else:
            self.send(Output.DATA, None)
            self.send(Output.TOPICS, None)

    def start_learning(self):
        self.topic_desc.clear()
        if self.corpus:
            self.progressBarInit()
            self.learning_thread = LearningThread(self.model, self.corpus.copy(),
                                                  result_callback=self.send_corpus,
                                                  progress_callback=self.progress)
            self.learning_thread.finished.connect(self.learning_finished)
            self.learning_thread.start()

    @QtCore.pyqtSlot()
    def stop_learning(self):

        if self.learning_thread and self.learning_thread.isRunning():
            self.learning_thread.terminate()
            self.learning_thread.wait()
            self.progressBarFinished()

    def send_corpus(self, corpus):
        self.send(Output.DATA, corpus)
        self.send_topic_by_id(0)

    def learning_finished(self):
        self.update_topics()
        self.progressBarFinished()

    def send_report(self):
        self.report_items(self.model.name, self.model.report())

    def send_topic_by_id(self, topic_id):
        self.send(Output.TOPICS, self.model.get_topics_table_by_id(topic_id))


class TopicViewerTreeWidgetItem(QtGui.QTreeWidgetItem):
    def __init__(self, topic_id, words, parent):
        super().__init__(parent)
        self.topic_id = topic_id
        self.words = words

        self.setText(0, '{:d}'.format(topic_id + 1))
        self.setText(1, ', '.join(words))


class TopicViewer(QtGui.QTreeWidget):
    """ Just keeps stuff organized. Holds topic visualization widget and related functions.

    """

    columns = ['Topic', 'Topic keywords']
    topicSelected = QtCore.pyqtSignal(int)

    def __init__(self):
        super().__init__()

        self.setColumnCount(len(self.columns))
        self.setHeaderLabels(self.columns)
        self.resize_columns()

    def resize_columns(self):
        for i in range(self.columnCount()):
            self.resizeColumnToContents(i)

    def show_model(self, topic_model):
        self.clear()
        for i in range(topic_model.num_topics):
            words = topic_model.get_top_words_by_id(i)
            it = TopicViewerTreeWidgetItem(i, words, self)
            self.addTopLevelItem(it)

        self.resize_columns()

    def selected_topic_changed(self):
        selected = self.selectedItems()
        if selected:
            topic_id = selected[0].topic_id
            self.setCurrentItem(self.topLevelItem(topic_id))
            self.topicSelected.emit(topic_id)
        else:
            self.topicSelected.emit(None)


class LearningThread(QtCore.QThread):
    def __init__(self, model, corpus, result_callback, **kwargs):
        super().__init__()
        self.result_callback = result_callback
        self.model = model
        self.corpus = corpus
        self.running = False
        self.kwargs = kwargs

    def run(self):
        self.running = True
        result = self.model.fit_transform(self.corpus, **self.kwargs)
        self.running = False
        self.result_callback(result)


if __name__ == '__main__':
    from PyQt4.QtGui import QApplication

    app = QApplication([])
    widget = OWTopicModeling()
    widget.set_data(Corpus.from_file('bookexcerpts'))
    # widget.set_data(Corpus.from_file('deerwester'))
    widget.show()
    app.exec()
    widget.saveSettings()
